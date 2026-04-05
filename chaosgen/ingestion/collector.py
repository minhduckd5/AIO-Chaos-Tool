import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from chaosgen.ingestion.prometheus_client import PrometheusClient
from chaosgen.ingestion.loki_client import LokiClient
from chaosgen.schemas.telemetry import TelemetryDataset, TelemetrySnapshot, TimeSeries, LogStream

logger = logging.getLogger(__name__)

DEFAULT_LOG_QUERY = '{namespace=~".+"}'


class TelemetryCollector:
    """
    Unified telemetry collector that aggregates metrics from Prometheus
    and logs from Loki into a single TelemetryDataset for ML consumption.
    """

    def __init__(
        self,
        prometheus: PrometheusClient,
        loki: Optional[LokiClient] = None,
        log_query: str = DEFAULT_LOG_QUERY,
    ):
        self.prometheus = prometheus
        self.loki = loki
        self.log_query = log_query

    def collect_baseline(
        self,
        duration_hours: int = 24,
        step: str = "60s",
        custom_queries: Optional[Dict[str, str]] = None,
    ) -> TelemetryDataset:
        """
        Collect a baseline telemetry dataset spanning *duration_hours*
        into the past from the current timestamp.
        """
        now = time.time()
        start = now - (duration_hours * 3600)

        logger.info(
            "Collecting baseline: %d hours (%s -> %s)",
            duration_hours,
            datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
            datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        )

        metrics = self._collect_metrics(start, now, step, custom_queries)
        logs = self._collect_logs(start, now)

        dataset = TelemetryDataset(
            metrics=metrics,
            logs=logs,
            collection_start=datetime.fromtimestamp(start, tz=timezone.utc),
            collection_end=datetime.fromtimestamp(now, tz=timezone.utc),
        )
        logger.info(
            "Baseline collected: %d series, %d total samples, %d log streams",
            len(dataset.metrics),
            dataset.total_samples,
            len(dataset.logs),
        )
        return dataset

    def collect_current_snapshot(self) -> TelemetrySnapshot:
        """Lightweight point-in-time snapshot for live monitoring."""
        from chaosgen.ingestion.prometheus_client import GOLDEN_SIGNAL_QUERIES

        metric_values: Dict[str, float] = {}
        for signal_name, promql in GOLDEN_SIGNAL_QUERIES.items():
            samples = self.prometheus.query(promql)
            for s in samples:
                label_key = s.labels.get("service") or s.labels.get("pod") or "unknown"
                metric_values[f"{signal_name}__{label_key}"] = s.value

        return TelemetrySnapshot(
            collected_at=datetime.now(tz=timezone.utc),
            metrics=metric_values,
        )

    def _collect_metrics(
        self,
        start: float,
        end: float,
        step: str,
        custom_queries: Optional[Dict[str, str]],
    ) -> List[TimeSeries]:
        all_series = self.prometheus.query_golden_signals(start, end, step)

        if custom_queries:
            for name, promql in custom_queries.items():
                series = self.prometheus.query_range(promql, start, end, step)
                for ts in series:
                    ts.metric_name = f"custom__{name}__{ts.metric_name}"
                all_series.extend(series)

        return all_series

    def _collect_logs(self, start: float, end: float) -> List[LogStream]:
        if not self.loki:
            logger.debug("Loki client not configured; skipping log collection.")
            return []
        return self.loki.query_range(self.log_query, start, end)
