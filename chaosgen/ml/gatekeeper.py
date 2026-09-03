"""
Incident Gatekeeper (P1 — `?? real ??`).

Implements the advisor's gatekeeping block: classify each anomaly cluster via a
frequency x severity matrix, with a strict log-correlation boost, so only REAL /
CHRONIC incidents proceed downstream. NOISE is dropped (audited); TRANSIENT is
monitor-only.

Design notes:
- `LookbackStateStore` is an abstraction with an in-memory default. Frequency can
  be accumulated across scrape batches without committing to infrastructure. P5
  swaps the adapter for SQLite without rewriting matrix logic.
- The strict log boost requires BOTH a metric-side error signal (an `error_rate`/
  `error_count` feature dominating the cluster) AND a severe log signal
  (`error/fatal/critical`). Warning/deprecation/info-only logs never boost.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Protocol, Tuple

from chaosgen.config.settings import GatekeeperSettings
from chaosgen.ml.canonical_features import extract_service_from_column, is_concrete_service
from chaosgen.schemas.incidents import IncidentCandidate, IncidentVerdict
from chaosgen.schemas.scenarios import AnomalyCluster, AnomalySeverity, AnomalySummary

logger = logging.getLogger(__name__)

_SEVERITY_NUMERIC: Dict[AnomalySeverity, float] = {
    AnomalySeverity.LOW: 0.25,
    AnomalySeverity.MEDIUM: 0.50,
    AnomalySeverity.HIGH: 0.75,
    AnomalySeverity.CRITICAL: 1.0,
}

_METRIC_ERROR_TOKENS = (
    "error_rate",
    "error_count",
    "error_ratio",
    "http_error",
    "http_errors",
    "5xx",
    "custom__error",
    "canonical__errors",
)


class LookbackState(Dict):
    """Lightweight cumulative state: {'samples': float, 'window_hours': float}."""


class LookbackStateStore(Protocol):
    """Abstraction for cross-batch frequency accumulation (P1 minimal, P5 SQLite)."""

    def get(self, key: str) -> Optional[Dict[str, float]]: ...

    def put(self, key: str, state: Dict[str, float]) -> None: ...


class InMemoryLookbackStateStore:
    """Default in-memory implementation. No external infrastructure (P1 scope)."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, float]] = {}

    def get(self, key: str) -> Optional[Dict[str, float]]:
        return self._data.get(key)

    def put(self, key: str, state: Dict[str, float]) -> None:
        self._data[key] = state

    def reset(self) -> None:
        self._data.clear()


class IncidentGatekeeper:
    """Routes anomaly clusters into NOISE / TRANSIENT / REAL / CHRONIC verdicts."""

    def __init__(self, settings: Optional[GatekeeperSettings] = None):
        self.settings = settings or GatekeeperSettings()

    # -- public API ---------------------------------------------------------

    def filter(
        self,
        clusters: List[AnomalyCluster],
        window_hours: float,
        summaries: Optional[List[AnomalySummary]] = None,
        state_store: Optional[LookbackStateStore] = None,
    ) -> Tuple[List[IncidentCandidate], int]:
        """
        Evaluate clusters and return (candidates, dropped_noise_count).

        NOISE clusters are excluded from the returned candidates but counted in
        the dropped total (with an audit log entry).
        """
        if window_hours <= 0:
            raise ValueError("window_hours must be > 0")

        summary_by_cluster = {s.source_cluster_id: s for s in (summaries or [])}

        candidates: List[IncidentCandidate] = []
        dropped = 0

        for cluster in clusters:
            summary = summary_by_cluster.get(cluster.cluster_id)
            service_target = self._resolve_service_target(cluster, summary)
            frequency = self._compute_frequency(
                cluster, window_hours, service_target, state_store
            )
            severity = _SEVERITY_NUMERIC.get(cluster.severity, 0.5)

            metric_error = self._has_metric_error_signal(cluster)
            severe_log = self._has_severe_log_signal(summary)
            log_correlated = metric_error and severe_log

            verdict, rationale = self._evaluate_verdict(
                frequency,
                severity,
                log_correlated,
                cluster=cluster,
                summary=summary,
            )

            if verdict == IncidentVerdict.NOISE:
                dropped += 1
                logger.info(
                    "Gatekeeper drop NOISE cluster=%s service=%s freq=%.2f sev=%.2f: %s",
                    cluster.cluster_id, service_target, frequency, severity, rationale,
                )
                continue

            candidates.append(IncidentCandidate(
                cluster_id=cluster.cluster_id,
                frequency=round(frequency, 4),
                severity=severity,
                log_correlated=log_correlated,
                service_target=service_target,
                metadata={
                    "metric_error_signal": metric_error,
                    "severe_log_signal": severe_log,
                    "sample_count": cluster.sample_count,
                    "window_hours": window_hours,
                    "error_pattern": summary.error_pattern if summary else None,
                },
                verdict=verdict,
                rationale=rationale,
            ))

        return candidates, dropped

    # -- decision matrix ----------------------------------------------------

    def _evaluate_verdict(
        self,
        freq: float,
        sev: float,
        log_correlated: bool,
        cluster: Optional[AnomalyCluster] = None,
        summary: Optional[AnomalySummary] = None,
    ) -> Tuple[IncidentVerdict, str]:
        s = self.settings
        freq_low = freq <= s.frequency_low_threshold
        freq_high = freq >= s.frequency_high_threshold
        sev_low = sev <= s.severity_low_threshold
        sev_high = sev >= s.severity_high_threshold

        if (
            s.service_error_boost
            and cluster is not None
            and self._has_metric_error_signal(cluster)
            and self._has_service_attribution(cluster, summary)
        ):
            service = self._resolve_service_target(cluster, summary) or "unknown"
            return IncidentVerdict.REAL, (
                f"Service-specific error signal on {service} "
                f"(freq={freq:.2f}/h, sev={sev:.2f}) -> real."
            )

        if freq_low and sev_low:
            return IncidentVerdict.NOISE, (
                f"Low frequency ({freq:.2f}/h) and low severity ({sev:.2f}) -> noise."
            )

        if freq_high and sev_high:
            if freq > s.frequency_high_threshold * 2:
                return IncidentVerdict.CHRONIC, (
                    f"High frequency ({freq:.2f}/h) and high severity ({sev:.2f}); "
                    f"recurring -> chronic (predictive maintenance candidate)."
                )
            return IncidentVerdict.REAL, (
                f"High frequency ({freq:.2f}/h) and high severity ({sev:.2f}) -> real."
            )

        # Gray / partial-high zone defaults to TRANSIENT.
        boost_enabled = s.log_correlation_boost and s.strict_log_boost
        if freq_high and log_correlated and boost_enabled:
            return IncidentVerdict.REAL, (
                f"Transient (freq={freq:.2f}/h, sev={sev:.2f}) boosted to REAL: "
                f"metric error signal + severe log correlation."
            )

        return IncidentVerdict.TRANSIENT, (
            f"Transient behavior (freq={freq:.2f}/h, sev={sev:.2f}); monitor only."
        )

    # -- signal helpers -----------------------------------------------------

    def _compute_frequency(
        self,
        cluster: AnomalyCluster,
        window_hours: float,
        service_target: Optional[str],
        state_store: Optional[LookbackStateStore],
    ) -> float:
        """Events per hour. Accumulates across batches when a state store is given."""
        if state_store is None:
            return cluster.sample_count / window_hours

        key = service_target or f"cluster:{cluster.cluster_id}"
        prev = state_store.get(key) or {"samples": 0.0, "window_hours": 0.0}
        total_samples = prev.get("samples", 0.0) + cluster.sample_count
        total_window = prev.get("window_hours", 0.0) + window_hours
        state_store.put(key, {"samples": total_samples, "window_hours": total_window})
        return total_samples / total_window if total_window > 0 else 0.0

    @staticmethod
    def _has_metric_error_signal(cluster: AnomalyCluster) -> bool:
        """True when an error_rate/error_count feature dominates the cluster."""
        for feat_name, _ in cluster.dominant_features:
            lowered = feat_name.lower()
            if any(token in lowered for token in _METRIC_ERROR_TOKENS):
                return True
        return False

    @staticmethod
    def _has_service_attribution(
        cluster: AnomalyCluster,
        summary: Optional[AnomalySummary],
    ) -> bool:
        """True when attribution resolves to a concrete microservice (not a signal bucket)."""
        if summary and is_concrete_service(summary.service_name):
            return True
        for name in cluster.affected_services or []:
            if is_concrete_service(name):
                return True
        for feat_name, _ in cluster.dominant_features:
            if extract_service_from_column(feat_name):
                return True
        return False

    def _has_severe_log_signal(self, summary: Optional[AnomalySummary]) -> bool:
        """True when the summary's error pattern contains a severe keyword."""
        if summary is None or not summary.error_pattern:
            return False
        pattern = summary.error_pattern.lower()
        return any(kw.lower() in pattern for kw in self.settings.severe_log_keywords)

    @staticmethod
    def _resolve_service_target(
        cluster: AnomalyCluster, summary: Optional[AnomalySummary]
    ) -> Optional[str]:
        if summary and summary.service_name and summary.service_name != "unknown":
            return summary.service_name
        if cluster.affected_services:
            first = cluster.affected_services[0]
            return first if first != "unknown" else None
        return None
