"""
Connection Verifier — re-probes observability endpoints after a bootstrap attempt
and gates the experiment pipeline.

ChaosOrchestrator.run_experiment() must call verify() before any fault injection.
"""

from __future__ import annotations

import logging
import time

from chaosgen.bootstrap.exceptions import TelemetryNotReadyError
from chaosgen.discovery.observability_probe import ObservabilityProbe
from chaosgen.schemas.discovery import ObservabilityProfile

logger = logging.getLogger(__name__)


class ConnectionVerifier:
    def __init__(
        self,
        prometheus_url: str = "http://localhost:9090",
        loki_url: str = "http://localhost:3100",
        grafana_url: str = "http://localhost:3000",
        jaeger_url: str = "http://localhost:16686",
        retries: int = 3,
        retry_delay_seconds: float = 5.0,
    ) -> None:
        self._probe = ObservabilityProbe(
            prometheus_url=prometheus_url,
            loki_url=loki_url,
            grafana_url=grafana_url,
            jaeger_url=jaeger_url,
        )
        self._retries = retries
        self._retry_delay = retry_delay_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(self, require_traces: bool = False) -> ObservabilityProfile:
        """
        Re-probe all endpoints. Retries up to `self._retries` times with
        `self._retry_delay` second delays (useful after helm install where
        pods take time to become ready).

        Raises TelemetryNotReadyError if minimum viable telemetry
        (metrics + logs) is still absent after all retries.
        """
        profile = ObservabilityProfile()

        for attempt in range(1, self._retries + 1):
            logger.info("Verifying observability (attempt %d/%d)...", attempt, self._retries)
            profile = self._probe.probe()

            if profile.telemetry_ready:
                if require_traces and not profile.has_traces:
                    logger.warning("Traces not available but not required for experiment pipeline.")
                logger.info(
                    "Telemetry verified: metrics=%s, logs=%s, traces=%s",
                    profile.has_metrics,
                    profile.has_logs,
                    profile.has_traces,
                )
                return profile

            if attempt < self._retries:
                logger.info(
                    "Telemetry not ready (metrics=%s, logs=%s). Retrying in %.0fs...",
                    profile.has_metrics,
                    profile.has_logs,
                    self._retry_delay,
                )
                time.sleep(self._retry_delay)

        missing = [t.value for t in profile.missing]
        raise TelemetryNotReadyError(
            f"Observability prerequisites not met after {self._retries} attempts. "
            f"Missing: {missing}. "
            "Run `chaosgen bootstrap` to install monitoring tooling, "
            "then retry."
        )

    def verify_or_warn(self) -> ObservabilityProfile:
        """
        Like verify() but logs a warning instead of raising if telemetry
        is missing. Used in dry-run mode or catalog-only generation where
        live data is not required.
        """
        try:
            return self.verify()
        except TelemetryNotReadyError as exc:
            logger.warning("[DRY-RUN] %s", exc)
            return self._probe.probe()
