import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from chaosgen.ingestion.prometheus_client import PrometheusClient
from chaosgen.ingestion.loki_client import LokiClient
from chaosgen.schemas.telemetry import TelemetryDataset, TelemetrySnapshot, TimeSeries, LogStream
from chaosgen.telemetry.pack_loader import (
    CANONICAL_SERVICE_KEY,
    PackQuery,
    ResolvedPackQueries,
    normalize_series_service_label,
)

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
        # MODIFIED: P8 — optional custom PromQL map from settings.ingest
        self.default_custom_queries: Optional[Dict[str, str]] = None
        # --- START MODIFICATION ---
        # Form-first telemetry packs (PromQL + LogQL metrics); None = legacy golden path
        self.pack_queries: Optional[ResolvedPackQueries] = None
        self.allow_legacy_golden: bool = True
        self.include_raw_logs: bool = True
        # --- END MODIFICATION ---

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

        merged = self._merge_custom_queries(custom_queries)
        metrics = self._collect_metrics(start, now, step, merged)
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

    def collect_range(
        self,
        start: datetime,
        end: datetime,
        step: str = "60s",
        custom_queries: Optional[Dict[str, str]] = None,
    ) -> TelemetryDataset:
        """
        Collect a telemetry dataset spanning from absolute datetime *start* to *end* (UTC).
        """
        if end <= start:
            raise ValueError(f"Collection end time ({end}) must be strictly after start time ({start}).")

        start_ts = start.timestamp()
        end_ts = end.timestamp()

        logger.info(
            "Collecting range: %s -> %s (%.2f hours)",
            start.isoformat(),
            end.isoformat(),
            (end_ts - start_ts) / 3600.0,
        )

        merged = self._merge_custom_queries(custom_queries)
        metrics = self._collect_metrics(start_ts, end_ts, step, merged)
        logs = self._collect_logs(start_ts, end_ts)

        start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

        dataset = TelemetryDataset(
            metrics=metrics,
            logs=logs,
            collection_start=start_utc,
            collection_end=end_utc,
        )
        logger.info(
            "Range collected: %d series, %d total samples, %d log streams",
            len(dataset.metrics),
            dataset.total_samples,
            len(dataset.logs),
        )
        return dataset

    def _merge_custom_queries(
        self, custom_queries: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, str]]:
        merged: Dict[str, str] = {}
        if self.default_custom_queries:
            merged.update(self.default_custom_queries)
        if custom_queries:
            merged.update(custom_queries)
        return merged or None

    def collect_current_snapshot(self) -> TelemetrySnapshot:
        """Lightweight point-in-time snapshot for live monitoring."""
        from chaosgen.ingestion.prometheus_client import GOLDEN_SIGNAL_QUERIES

        metric_values: Dict[str, float] = {}
        query_map = dict(GOLDEN_SIGNAL_QUERIES)
        if self.pack_queries:
            for q in self.pack_queries.prometheus:
                query_map[q.signal] = q.query

        for signal_name, promql in query_map.items():
            samples = self.prometheus.query(promql)
            for s in samples:
                labels = normalize_series_service_label(s.labels, "service")
                label_key = labels.get(CANONICAL_SERVICE_KEY) or "unknown"
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
        # --- START MODIFICATION ---
        # Packs first (same start/end/step for Prom + Loki), then custom overrides,
        # then optional legacy golden/infra fallback.
        all_series: List[TimeSeries] = []
        pack_had_series = False

        if self.pack_queries:
            pack_series = self._collect_pack_series(start, end, step, self.pack_queries)
            all_series.extend(pack_series)
            pack_had_series = bool(pack_series)
            logger.info(
                "Pack queries (%s): %d series",
                ",".join(self.pack_queries.pack_ids),
                len(pack_series),
            )

        use_legacy = self.allow_legacy_golden and (
            self.pack_queries is None or not pack_had_series
        )
        if use_legacy:
            golden = self.prometheus.query_golden_signals(start, end, step)
            if not golden:
                logger.warning(
                    "Golden-signal PromQL returned 0 series (http_requests_total / container_* "
                    "may not exist on this Prometheus). Falling back to infra fallback metrics."
                )
                golden = self.prometheus.query_fallback_infra(start, end, step)
            all_series.extend(golden)
        elif self.pack_queries is not None and not pack_had_series:
            logger.warning(
                "Telemetry packs returned 0 series and allow_legacy_golden=False; "
                "continuing with custom_promql only"
            )

        if custom_queries:
            for name, promql in custom_queries.items():
                try:
                    series = self.prometheus.query_range(promql, start, end, step)
                    for ts in series:
                        ts.metric_name = f"custom__{name}__{ts.metric_name}"
                    all_series.extend(series)
                except Exception as exc:
                    logger.warning(
                        "Custom PromQL %r failed (%s); skipping series", name, exc
                    )

        return all_series
        # --- END MODIFICATION ---

    def _collect_pack_series(
        self,
        start: float,
        end: float,
        step: str,
        packs: ResolvedPackQueries,
    ) -> List[TimeSeries]:
        """Execute pack PromQL + LogQL metric queries with identical time bounds."""
        collected: List[TimeSeries] = []
        for q in packs.prometheus:
            try:
                series = self.prometheus.query_range(q.query, start, end, step)
                collected.extend(self._annotate_pack_series(series, q))
            except Exception as exc:
                logger.warning(
                    "Pack PromQL %s/%s failed (%s); skipping",
                    q.pack_id,
                    q.signal,
                    exc,
                )

        if packs.loki and not self.loki:
            logger.warning(
                "Pack includes Loki LogQL metrics but Loki client is not configured"
            )
        elif self.loki:
            for q in packs.loki:
                try:
                    # MODIFIED: identical start/end/step as Prometheus (Gemini pin)
                    series = self.loki.query_metric_range(q.query, start, end, step)
                    collected.extend(self._annotate_pack_series(series, q))
                except Exception as exc:
                    logger.warning(
                        "Pack LogQL %s/%s failed (%s); skipping",
                        q.pack_id,
                        q.signal,
                        exc,
                    )
        return collected

    @staticmethod
    def _annotate_pack_series(
        series: List[TimeSeries], query: PackQuery
    ) -> List[TimeSeries]:
        """Rename series to pack__{signal} and canonicalize service label."""
        annotated: List[TimeSeries] = []
        for ts in series:
            labels = normalize_series_service_label(ts.labels, query.service_label)
            # Keep only canonical service for FeatureEngineer column identity
            slim: Dict[str, str] = {}
            if CANONICAL_SERVICE_KEY in labels:
                slim[CANONICAL_SERVICE_KEY] = labels[CANONICAL_SERVICE_KEY]
            annotated.append(
                TimeSeries(
                    metric_name=query.metric_name,
                    labels=slim,
                    samples=ts.samples,
                )
            )
        return annotated

    def _collect_logs(self, start: float, end: float) -> List[LogStream]:
        if not self.include_raw_logs:
            return []
        if not self.loki:
            logger.debug("Loki client not configured; skipping log collection.")
            return []
        return self.loki.query_range(self.log_query, start, end)
