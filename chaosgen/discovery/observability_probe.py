"""
Observability Probe — pluggable, authenticated probing with error classification.

Each tool has its own Prober class implementing a common interface. Auth credentials
are resolved from .env via token_ref / password_ref in AuthConfig. Retries use
exponential backoff. Probe outcomes are classified as REACHABLE, AUTH_REJECTED,
TIMEOUT, CONNECTION_REFUSED, DNS_FAILURE, or UNKNOWN_ERROR.
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import requests

from chaosgen.schemas.discovery import (
    DiscoverySignal,
    HintSource,
    ObservabilityProfile,
    ObservabilityTool,
    ProbeOutcome,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 3.0
_MAX_RETRIES = 3

DEFAULT_URLS: dict[ObservabilityTool, str] = {
    ObservabilityTool.PROMETHEUS: "http://localhost:9090",
    ObservabilityTool.LOKI: "http://localhost:3100",
    ObservabilityTool.GRAFANA: "http://localhost:3000",
    ObservabilityTool.JAEGER: "http://localhost:16686",
    ObservabilityTool.OTEL_COLLECTOR: "http://localhost:13133",
}


# ---------------------------------------------------------------------------
# Probe result
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    reachable: bool
    outcome: ProbeOutcome
    endpoint: str
    message: str = ""


# ---------------------------------------------------------------------------
# Auth session builder
# ---------------------------------------------------------------------------


def _build_session(auth: "AuthConfig | None") -> requests.Session:
    """Construct a requests.Session with credentials resolved from .env."""
    session = requests.Session()
    if auth is None or auth.auth_type == "none":
        return session

    from chaosgen.config.secrets import get_key

    if auth.auth_type == "bearer":
        token = get_key(auth.token_ref) if auth.token_ref else ""
        session.headers["Authorization"] = f"Bearer {token}"

    elif auth.auth_type == "basic":
        password = get_key(auth.password_ref) if auth.password_ref else ""
        session.auth = (auth.username or "", password)

    elif auth.auth_type == "mtls":
        if auth.cert_path and auth.key_path:
            session.cert = (auth.cert_path, auth.key_path)
        if auth.ca_path:
            session.verify = auth.ca_path

    return session


# ---------------------------------------------------------------------------
# Prober protocol + implementations
# ---------------------------------------------------------------------------


@runtime_checkable
class ObservabilityProber(Protocol):
    tool: ObservabilityTool

    def probe(self, url: str, session: requests.Session) -> ProbeResult: ...


class PrometheusProber:
    tool = ObservabilityTool.PROMETHEUS

    def probe(self, url: str, session: requests.Session) -> ProbeResult:
        url = url.rstrip("/")
        resp = session.get(
            f"{url}/api/v1/query",
            params={"query": "up"},
            timeout=_DEFAULT_TIMEOUT,
        )
        if resp.status_code in (401, 403):
            return ProbeResult(False, ProbeOutcome.AUTH_REJECTED, url, f"HTTP {resp.status_code}")
        if resp.status_code == 200 and resp.json().get("status") == "success":
            return ProbeResult(True, ProbeOutcome.REACHABLE, url)
        return ProbeResult(False, ProbeOutcome.UNKNOWN_ERROR, url, f"HTTP {resp.status_code}")


class LokiProber:
    tool = ObservabilityTool.LOKI

    def probe(self, url: str, session: requests.Session) -> ProbeResult:
        url = url.rstrip("/")
        resp = session.get(f"{url}/ready", timeout=_DEFAULT_TIMEOUT)
        if resp.status_code in (401, 403):
            return ProbeResult(False, ProbeOutcome.AUTH_REJECTED, url, f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            return ProbeResult(True, ProbeOutcome.REACHABLE, url)
        return ProbeResult(False, ProbeOutcome.UNKNOWN_ERROR, url, f"HTTP {resp.status_code}")


class GrafanaProber:
    tool = ObservabilityTool.GRAFANA

    def probe(self, url: str, session: requests.Session) -> ProbeResult:
        url = url.rstrip("/")
        resp = session.get(f"{url}/api/health", timeout=_DEFAULT_TIMEOUT)
        if resp.status_code in (401, 403):
            return ProbeResult(False, ProbeOutcome.AUTH_REJECTED, url, f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            return ProbeResult(True, ProbeOutcome.REACHABLE, url)
        return ProbeResult(False, ProbeOutcome.UNKNOWN_ERROR, url, f"HTTP {resp.status_code}")


class JaegerProber:
    tool = ObservabilityTool.JAEGER

    def probe(self, url: str, session: requests.Session) -> ProbeResult:
        url = url.rstrip("/")
        resp = session.get(f"{url}/api/services", timeout=_DEFAULT_TIMEOUT)
        if resp.status_code in (401, 403):
            return ProbeResult(False, ProbeOutcome.AUTH_REJECTED, url, f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            return ProbeResult(True, ProbeOutcome.REACHABLE, url)
        return ProbeResult(False, ProbeOutcome.UNKNOWN_ERROR, url, f"HTTP {resp.status_code}")


class OtelProber:
    tool = ObservabilityTool.OTEL_COLLECTOR

    def probe(self, url: str, session: requests.Session) -> ProbeResult:
        url = url.rstrip("/")
        resp = session.get(f"{url}/", timeout=_DEFAULT_TIMEOUT)
        if resp.status_code in (401, 403):
            return ProbeResult(False, ProbeOutcome.AUTH_REJECTED, url, f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            return ProbeResult(True, ProbeOutcome.REACHABLE, url)
        return ProbeResult(False, ProbeOutcome.UNKNOWN_ERROR, url, f"HTTP {resp.status_code}")


# ---------------------------------------------------------------------------
# Prober registry
# ---------------------------------------------------------------------------

PROBER_REGISTRY: list[type] = [
    PrometheusProber,
    LokiProber,
    GrafanaProber,
    JaegerProber,
    OtelProber,
]


# ---------------------------------------------------------------------------
# Retry with exponential backoff + error classification
# ---------------------------------------------------------------------------


def _probe_with_backoff(
    prober: ObservabilityProber,
    url: str,
    session: requests.Session,
    max_retries: int = _MAX_RETRIES,
) -> ProbeResult:
    """
    Probe with exponential backoff (1s, 2s, 4s).
    AUTH_REJECTED short-circuits — no retry (tool exists, creds wrong).
    """
    result = ProbeResult(False, ProbeOutcome.UNKNOWN_ERROR, url)

    for attempt in range(max_retries):
        try:
            result = prober.probe(url, session)
            if result.reachable:
                return result
            if result.outcome == ProbeOutcome.AUTH_REJECTED:
                return result
        except requests.exceptions.ConnectionError:
            result = ProbeResult(False, ProbeOutcome.CONNECTION_REFUSED, url, "Connection refused")
        except requests.exceptions.Timeout:
            result = ProbeResult(False, ProbeOutcome.TIMEOUT, url, "Request timed out")
        except socket.gaierror as exc:
            return ProbeResult(False, ProbeOutcome.DNS_FAILURE, url, str(exc))
        except Exception as exc:
            result = ProbeResult(False, ProbeOutcome.UNKNOWN_ERROR, url, str(exc))

        if attempt < max_retries - 1:
            delay = 2 ** attempt
            logger.debug(
                "%s probe attempt %d/%d failed (%s). Retrying in %ds...",
                prober.tool.value, attempt + 1, max_retries, result.outcome.value, delay,
            )
            time.sleep(delay)

    return result


# ---------------------------------------------------------------------------
# Orchestrating probe
# ---------------------------------------------------------------------------


class ObservabilityProbe:
    """
    Probes all known observability tools using pluggable probers.
    Accepts user hints for custom URLs and auth; falls back to defaults.
    """

    def __init__(
        self,
        hints: list | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._hint_map: dict[ObservabilityTool, Any] = {}
        if hints:
            for h in hints:
                self._hint_map[h.tool] = h
        self._max_retries = max_retries

    def probe(self) -> tuple[ObservabilityProfile, list[DiscoverySignal]]:
        """
        Run all probers. Returns (ObservabilityProfile, list of DiscoverySignals).
        """
        detected: list[ObservabilityTool] = []
        missing: list[ObservabilityTool] = []
        signals: list[DiscoverySignal] = []

        results: dict[ObservabilityTool, ProbeResult] = {}

        for prober_cls in PROBER_REGISTRY:
            prober = prober_cls()
            tool = prober.tool
            hint = self._hint_map.get(tool)

            url = hint.url if hint else DEFAULT_URLS.get(tool, "")
            auth = hint.auth if hint else None
            source = HintSource.USER_OVERRIDE if hint else HintSource.HEURISTIC_FALLBACK

            session = _build_session(auth)
            result = _probe_with_backoff(prober, url, session, self._max_retries)
            results[tool] = result

            if result.reachable:
                detected.append(tool)
                logger.info("%s detected at %s", tool.value, url)
            else:
                missing.append(tool)
                logger.debug("%s not reachable at %s: %s", tool.value, url, result.outcome.value)

            signals.append(DiscoverySignal(
                source=source,
                key=tool.value,
                value=result.outcome.value,
                confidence=1.0 if result.reachable else 0.0,
                probe_outcome=result.outcome,
                message=result.message or f"{tool.value} @ {url}: {result.outcome.value}",
            ))

        prom = results.get(ObservabilityTool.PROMETHEUS)
        loki = results.get(ObservabilityTool.LOKI)
        jaeger = results.get(ObservabilityTool.JAEGER)
        otel = results.get(ObservabilityTool.OTEL_COLLECTOR)

        profile = ObservabilityProfile(
            has_metrics=bool(prom and prom.reachable),
            metrics_endpoint=prom.endpoint if prom and prom.reachable else None,
            has_logs=bool(loki and loki.reachable),
            logs_endpoint=loki.endpoint if loki and loki.reachable else None,
            has_traces=bool((jaeger and jaeger.reachable) or (otel and otel.reachable)),
            traces_endpoint=(
                jaeger.endpoint if jaeger and jaeger.reachable
                else (otel.endpoint if otel and otel.reachable else None)
            ),
            detected=detected,
            missing=missing,
        )

        return profile, signals
