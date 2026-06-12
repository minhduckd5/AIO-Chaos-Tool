"""Factory helpers for live telemetry collectors."""

from __future__ import annotations

from chaosgen.config.secrets import load_secrets
from chaosgen.config.settings import ChaosGenSettings
from chaosgen.config.telemetry_endpoints import resolve_loki_url, resolve_prometheus_url
from chaosgen.ingestion.collector import TelemetryCollector, DEFAULT_LOG_QUERY
from chaosgen.ingestion.loki_client import LokiClient
from chaosgen.ingestion.prometheus_client import PrometheusClient


def build_prometheus_client(
    url: str | None = None,
    settings: ChaosGenSettings | None = None,
) -> PrometheusClient:
    secrets = load_secrets()
    return PrometheusClient(
        url or resolve_prometheus_url(settings),
        bearer_token=secrets.get("PROMETHEUS_TOKEN"),
    )


def build_loki_client(
    url: str | None = None,
    settings: ChaosGenSettings | None = None,
) -> LokiClient:
    secrets = load_secrets()
    return LokiClient(
        url or resolve_loki_url(settings),
        bearer_token=secrets.get("LOKI_TOKEN"),
    )


def build_telemetry_collector(
    settings: ChaosGenSettings | None = None,
    log_query: str = DEFAULT_LOG_QUERY,
    prom_url: str | None = None,
    loki_url: str | None = None,
) -> TelemetryCollector:
    prom = build_prometheus_client(prom_url, settings)
    loki = build_loki_client(loki_url, settings)
    return TelemetryCollector(prometheus=prom, loki=loki, log_query=log_query)


def check_live_stack(
    settings: ChaosGenSettings | None = None,
    prom_url: str | None = None,
    loki_url: str | None = None,
) -> dict[str, str]:
    """Health-check Prometheus and Loki with auth-aware probes."""
    prom = build_prometheus_client(prom_url, settings)
    loki = build_loki_client(loki_url, settings)
    prom_ok, prom_msg = prom.probe()
    loki_ok, loki_msg = loki.probe()
    return {
        "prometheus": prom_msg if prom_ok else f"FAIL: {prom_msg}",
        "loki": loki_msg if loki_ok else f"FAIL: {loki_msg}",
    }
