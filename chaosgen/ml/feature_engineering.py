import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from chaosgen.schemas.telemetry import TelemetryDataset, TimeSeries, LogStream

logger = logging.getLogger(__name__)

# --- START MODIFICATION ---
# Entity-keyed long-format schema: rows are (timestamp, service), columns are
# fixed signal__stat names. Service identity lives in the index, never in a
# column name, so the trained column set is independent of which services or
# packs happened to answer in a given collection window.

ENTITY_KEYED_LAYOUT = "entity_keyed"
WIDE_LAYOUT = "wide"

TIMESTAMP_LEVEL = "timestamp"
SERVICE_LEVEL = "service"

#: Reserved entity for series that carry no service identity (cluster-wide
#: aggregates such as KSM deployment counts or blackbox probes).
SYSTEM_ENTITY = "_system"

#: Bucket for metric names that match no declared signal (foreign exports,
#: user ``custom_promql`` keys). Pooled rather than dropped.
OTHER_SIGNAL = "other"

ENTITY_STATS: Tuple[str, ...] = ("mean", "std", "roc", "p95")

# Signals declared by the bundled telemetry packs. The registry is fixed at
# import time, so enabling extra_packs changes which columns carry data but
# never changes the column set itself.
_BOUTIQUE_SIGNALS = (
    "request_rate",
    "error_rate",
    "error_ratio",
    "latency_p95",
    "latency_p99",
    "replicas_available",
    "frontend_health",
    "span_error_rate",
)
_OTEL_SIGNALS = ("otel_request_rate", "otel_error_rate")
_CADVISOR_SIGNALS = (
    "cpu_usage",
    "memory_usage",
    "saturation_cpu",
    "network_rx_bytes",
    "network_tx_bytes",
)
_LOKI_PACK_SIGNALS = ("log_error_rate", "log_volume")
# Legacy golden-signal collector (prometheus_client.GOLDEN_SIGNAL_QUERIES).
_GOLDEN_SIGNALS = ("latency_p50",)
# Fallback infra collector (prometheus_client.FALLBACK_INFRA_QUERIES).
_INFRA_SIGNALS = (
    "infra_up",
    "infra_node_cpu",
    "infra_node_memory_avail",
    "infra_http_server_duration_count",
    "infra_rpc_server_duration_count",
)
#: Derived from raw Loki log lines inside this module (distinct from the
#: loki_system pack's LogQL rates, which keep their own ``log_*`` stems).
_LOGLINE_SIGNALS = (
    "logline_volume",
    "logline_error_count",
    "logline_error_rate",
    "logline_unique_patterns",
)

ENTITY_SIGNALS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        _BOUTIQUE_SIGNALS
        + _OTEL_SIGNALS
        + _CADVISOR_SIGNALS
        + _LOKI_PACK_SIGNALS
        + _GOLDEN_SIGNALS
        + _INFRA_SIGNALS
        + _LOGLINE_SIGNALS
        + (OTHER_SIGNAL,)
    )
)

ENTITY_FEATURE_COLUMNS: List[str] = [
    f"{signal}__{stat}" for signal in ENTITY_SIGNALS for stat in ENTITY_STATS
]

_ENTITY_SIGNAL_SET = frozenset(ENTITY_SIGNALS)

#: Label candidates checked in order when resolving the entity key. Mirrors
#: ``chaosgen.telemetry.pack_loader.normalize_series_service_label`` so pack and
#: non-pack series attribute identically. ``deployment`` is deliberately absent:
#: KSM deployment series stay on ``_system``.
_ENTITY_LABEL_CANDIDATES = (
    "service",
    "service_name",
    "app",
    "pod",
    "container_name",
    "container",
    "destination_workload",
)

# --- START MODIFICATION ---
# Per-signal entity policy. Values:
#   "label"  — first non-empty candidate in _ENTITY_LABEL_CANDIDATES, else _system
#   "system" — always _system; labels are ignored even if a service token is present
#
# replicas_available and frontend_health are cluster-scoped aggregates (KSM
# deployment counts / blackbox probe). Mapping `deployment` → service is out of
# scope, so these two are authored as always-_system, not "usually".
SIGNAL_ENTITY_POLICY: Dict[str, str] = {
    "request_rate": "label",
    "error_rate": "label",
    "error_ratio": "label",
    "latency_p95": "label",
    "latency_p99": "label",
    "span_error_rate": "label",
    "replicas_available": "system",
    "frontend_health": "system",
    "otel_request_rate": "label",
    "otel_error_rate": "label",
    "cpu_usage": "label",
    "memory_usage": "label",
    "saturation_cpu": "label",
    "network_rx_bytes": "label",
    "network_tx_bytes": "label",
    "log_error_rate": "label",
    "log_volume": "label",
    "latency_p50": "label",
    "infra_up": "label",
    "infra_node_cpu": "label",
    "infra_node_memory_avail": "label",
    "infra_http_server_duration_count": "label",
    "infra_rpc_server_duration_count": "label",
    "logline_volume": "label",
    "logline_error_count": "label",
    "logline_error_rate": "label",
    "logline_unique_patterns": "label",
    OTHER_SIGNAL: "label",
}

SYSTEM_SCOPED_SIGNALS: frozenset[str] = frozenset(
    name for name, policy in SIGNAL_ENTITY_POLICY.items() if policy == "system"
)
# --- END MODIFICATION ---

_ERROR_LOG_LEVELS = ("ERROR", "FATAL", "CRITICAL", "PANIC")


class FeatureLayoutMismatchError(ValueError):
    """
    A persisted model was trained on a different feature layout than the live
    matrix. Callers must refit rather than zero-pad across incompatible schemas.
    """


def resolve_entity_key(labels: Optional[Mapping[str, str]]) -> str:
    """Return the entity (service) key for a series/stream label set."""
    for key in _ENTITY_LABEL_CANDIDATES:
        raw = (labels or {}).get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return SYSTEM_ENTITY


def entity_key_for_signal(
    stem: str, labels: Optional[Mapping[str, str]]
) -> str:
    """
    Resolve the entity key for one declared signal.

    Authored rules (see :data:`SIGNAL_ENTITY_POLICY`):
      * ``replicas_available`` / ``frontend_health`` → always ``_system``
      * every other declared signal → :func:`resolve_entity_key`
      * undeclared stems inherit the ``other`` policy (label, else ``_system``)
    """
    policy = SIGNAL_ENTITY_POLICY.get(stem, SIGNAL_ENTITY_POLICY[OTHER_SIGNAL])
    if policy == "system":
        return SYSTEM_ENTITY
    return resolve_entity_key(labels)


def resolve_signal_stem(metric_name: str) -> str:
    """
    Map a raw ``TimeSeries.metric_name`` to a declared signal stem.

    Recognised shapes:
      ``pack__{signal}``                  -> ``{signal}``
      ``infra__{signal}__{promql}``       -> ``infra_{signal}``
      ``{signal}__{promql}`` (golden)     -> ``{signal}``
    Anything else pools into :data:`OTHER_SIGNAL`.
    """
    name = str(metric_name).strip()
    if name.startswith("pack__"):
        stem = name[len("pack__") :]
    elif name.startswith("infra__"):
        parts = name.split("__")
        stem = f"infra_{parts[1]}" if len(parts) >= 2 else OTHER_SIGNAL
    else:
        stem = name.split("__", 1)[0]
    return stem if stem in _ENTITY_SIGNAL_SET else OTHER_SIGNAL


def detect_feature_layout(features: pd.DataFrame) -> str:
    """Return the layout of an existing feature matrix."""
    index = features.index
    if isinstance(index, pd.MultiIndex) and SERVICE_LEVEL in list(index.names or []):
        return ENTITY_KEYED_LAYOUT
    return WIDE_LAYOUT


def split_feature_index(
    index: pd.Index,
) -> Tuple[pd.Index, Optional[np.ndarray]]:
    """
    Split a feature index into (timestamps, entities).

    Entities is ``None`` for the legacy wide layout.
    """
    names = list(index.names or [])
    if isinstance(index, pd.MultiIndex) and SERVICE_LEVEL in names:
        timestamps = index.get_level_values(TIMESTAMP_LEVEL if TIMESTAMP_LEVEL in names else 0)
        entities = index.get_level_values(SERVICE_LEVEL)
        timestamps = timestamps.copy()
        timestamps.name = TIMESTAMP_LEVEL
        return timestamps, np.asarray(entities, dtype=object)
    return index, None
# --- END MODIFICATION ---


class FeatureEngineer:
    """
    Transforms raw TelemetryDataset into a feature matrix suitable
    for unsupervised anomaly detection.

    Pipeline: raw samples -> windowed aggregation -> rolling stats ->
              z-score filtering -> merged feature DataFrame.

    Two layouts are supported:
      * ``entity_keyed`` (default): MultiIndex ``(timestamp, service)`` rows and
        the fixed :data:`ENTITY_FEATURE_COLUMNS` column set.
      * ``wide`` (legacy escape hatch): DatetimeIndex rows and dynamic
        ``{metric}__{labels}__{stat}`` columns.
    """

    def __init__(
        self,
        window_size: int = 300,
        step: int = 60,
        zscore_threshold: float = 5.0,
        settings: Optional[Any] = None,
        layout: Optional[str] = None,
    ):
        """
        Args:
            window_size: Aggregation window in seconds.
            step: Slide step in seconds.
            zscore_threshold: Per-feature |z| cap applied to assembled feature
                rows *before* IsolationForest. Intended as coarse sensor-spike
                rejection, not as the anomaly detector. Values near 3.0 strip
                genuine quiet-baseline error spikes (same rows IF needs); 5.0
                is the validated product default after lab E2E evidence.
            layout: ``entity_keyed`` or ``wide``; overrides settings when given.
        """
        if settings is not None:
            self.window_size = getattr(settings, "rolling_window_seconds", window_size)
            self.step = getattr(settings, "resample_step_seconds", step)
            self.zscore_threshold = getattr(settings, "zscore_threshold", zscore_threshold)
            settings_layout = getattr(settings, "layout", ENTITY_KEYED_LAYOUT)
        else:
            self.window_size = window_size
            self.step = step
            self.zscore_threshold = zscore_threshold
            settings_layout = ENTITY_KEYED_LAYOUT

        resolved_layout = str(layout or settings_layout or ENTITY_KEYED_LAYOUT)
        if resolved_layout not in (ENTITY_KEYED_LAYOUT, WIDE_LAYOUT):
            raise ValueError(
                f"Unknown feature layout {resolved_layout!r}; "
                f"expected {ENTITY_KEYED_LAYOUT!r} or {WIDE_LAYOUT!r}"
            )
        self.layout = resolved_layout

    def transform(self, dataset: TelemetryDataset) -> pd.DataFrame:
        """Convert a full TelemetryDataset into a feature matrix."""
        if self.layout == ENTITY_KEYED_LAYOUT:
            return self._transform_entity_keyed(dataset)
        return self._transform_wide(dataset)

    # -- entity-keyed layout ------------------------------------------------

    def _transform_entity_keyed(self, dataset: TelemetryDataset) -> pd.DataFrame:
        """Build the MultiIndex (timestamp, service) x fixed-column matrix."""
        buckets: Dict[Tuple[str, str, str], List[pd.Series]] = {}
        unmapped_names: set[str] = set()

        for ts in dataset.metrics:
            if not ts.samples:
                continue
            stem = resolve_signal_stem(ts.metric_name)
            if stem == OTHER_SIGNAL:
                unmapped_names.add(str(ts.metric_name))
            entity = entity_key_for_signal(stem, ts.labels)
            raw = self._raw_series(ts)
            if raw.empty:
                continue
            for stat_name, stat_series in self._compute_rolling_stats(raw).items():
                buckets.setdefault((entity, stem, stat_name), []).append(stat_series)

        for entity, log_frame in self._logline_frames(dataset.logs).items():
            for signal in log_frame.columns:
                raw = log_frame[signal].astype(float)
                if raw.empty:
                    continue
                for stat_name, stat_series in self._compute_rolling_stats(raw).items():
                    buckets.setdefault((entity, str(signal), stat_name), []).append(
                        stat_series
                    )

        if not buckets:
            logger.warning("No metric features extracted; returning empty DataFrame.")
            return pd.DataFrame()

        if unmapped_names:
            logger.warning(
                "Entity-keyed layout pooled %d metric name(s) into '%s__*' "
                "(no declared signal): %s",
                len(unmapped_names),
                OTHER_SIGNAL,
                ", ".join(sorted(unmapped_names)[:20]),
            )

        grid = self._time_grid(buckets.values())
        if grid is None or grid.empty:
            return pd.DataFrame()

        entities = sorted({key[0] for key in buckets})
        frames: List[pd.DataFrame] = []
        frame_keys: List[str] = []

        for entity in entities:
            frame = pd.DataFrame(0.0, index=grid, columns=ENTITY_FEATURE_COLUMNS)
            for (bucket_entity, stem, stat), series_list in buckets.items():
                if bucket_entity != entity:
                    continue
                column = f"{stem}__{stat}"
                if column not in frame.columns:
                    continue
                frame[column] = self._pool_to_grid(series_list, grid)
            frame = frame.fillna(0.0)
            observed = frame.loc[(frame != 0.0).any(axis=1)]
            if observed.empty:
                continue
            frames.append(observed)
            frame_keys.append(entity)

        if not frames:
            logger.warning("Entity-keyed layout produced no observed rows.")
            return pd.DataFrame()

        matrix = pd.concat(
            frames, keys=frame_keys, names=[SERVICE_LEVEL, TIMESTAMP_LEVEL]
        )
        matrix = matrix.swaplevel(0, 1).sort_index()
        matrix.index.names = [TIMESTAMP_LEVEL, SERVICE_LEVEL]

        matrix = self._zscore_filter(matrix)
        logger.info(
            "Feature matrix shape: %s (layout=%s, entities=%d, columns=%d fixed)",
            matrix.shape,
            ENTITY_KEYED_LAYOUT,
            len(frame_keys),
            len(ENTITY_FEATURE_COLUMNS),
        )
        return matrix

    @staticmethod
    def _raw_series(ts: TimeSeries) -> pd.Series:
        idx = pd.to_datetime(ts.timestamps, unit="s", utc=True)
        raw = pd.Series(ts.values, index=idx, dtype=float).sort_index()
        if raw.index.has_duplicates:
            raw = raw[~raw.index.duplicated(keep="last")]
        return raw

    def _time_grid(
        self, bucket_values: Iterable[List[pd.Series]]
    ) -> Optional[pd.DatetimeIndex]:
        """Shared regular timestamp grid so every entity aligns on the same rows."""
        start: Optional[pd.Timestamp] = None
        end: Optional[pd.Timestamp] = None
        for series_list in bucket_values:
            for series in series_list:
                if series.empty:
                    continue
                first = series.index[0]
                last = series.index[-1]
                start = first if start is None or first < start else start
                end = last if end is None or last > end else end
        if start is None or end is None:
            return None
        freq = f"{self.step}s"
        return pd.date_range(
            start=pd.Timestamp(start).floor(freq),
            end=pd.Timestamp(end).ceil(freq),
            freq=freq,
            tz="UTC",
        )

    def _pool_to_grid(
        self, series_list: List[pd.Series], grid: pd.DatetimeIndex
    ) -> pd.Series:
        """Resample each series onto *grid* and pool duplicates with max."""
        aligned: List[pd.Series] = []
        freq = f"{self.step}s"
        for series in series_list:
            if series.empty:
                continue
            resampled = series.resample(freq).mean()
            resampled = resampled.reindex(
                resampled.index.union(grid)
            ).sort_index().ffill().reindex(grid)
            aligned.append(resampled)
        if not aligned:
            return pd.Series(0.0, index=grid)
        if len(aligned) == 1:
            return aligned[0].fillna(0.0)
        stacked = pd.concat(aligned, axis=1)
        return stacked.max(axis=1).fillna(0.0)

    def _logline_frames(self, log_streams: List[LogStream]) -> Dict[str, pd.DataFrame]:
        """Per-entity log-line features bucketed at the resample step."""
        if not log_streams:
            return {}

        records: List[Dict[str, Any]] = []
        for stream in log_streams:
            stream_entity = resolve_entity_key(stream.stream_labels)
            for entry in stream.entries:
                entity = stream_entity
                if entity == SYSTEM_ENTITY and entry.labels:
                    entity = resolve_entity_key(entry.labels)
                records.append(
                    {
                        "timestamp": entry.timestamp,
                        SERVICE_LEVEL: entity,
                        "level": (entry.level or "UNKNOWN").upper(),
                        "message": entry.message,
                    }
                )

        if not records:
            return {}

        raw = pd.DataFrame(records)
        raw["timestamp"] = pd.to_datetime(raw["timestamp"], unit="s", utc=True)
        raw["is_error"] = raw["level"].isin(_ERROR_LOG_LEVELS)

        frames: Dict[str, pd.DataFrame] = {}
        freq = f"{self.step}s"
        for entity, group in raw.groupby(SERVICE_LEVEL, sort=True):
            indexed = group.set_index("timestamp").sort_index()
            bucketed = indexed.resample(freq)
            volume = bucketed.size().astype(float)
            error_count = bucketed["is_error"].sum().astype(float)
            unique_patterns = bucketed["message"].nunique().astype(float)
            frame = pd.DataFrame(
                {
                    "logline_volume": volume,
                    "logline_error_count": error_count,
                    "logline_error_rate": error_count / volume.replace(0.0, 1.0),
                    "logline_unique_patterns": unique_patterns,
                }
            ).fillna(0.0)
            frames[str(entity)] = frame
        return frames

    # -- legacy wide layout -------------------------------------------------

    def _transform_wide(self, dataset: TelemetryDataset) -> pd.DataFrame:
        """Legacy dynamic-column layout retained for debugging / A-B runs."""
        metric_df = self._transform_metrics(dataset.metrics)
        log_df = self._extract_log_features(dataset.logs)

        if metric_df.empty:
            logger.warning("No metric features extracted; returning empty DataFrame.")
            return pd.DataFrame()

        if not log_df.empty:
            metric_df = metric_df.join(log_df, how="left").fillna(0)

        metric_df = self._zscore_filter(metric_df)
        logger.info("Feature matrix shape: %s (layout=%s)", metric_df.shape, WIDE_LAYOUT)
        return metric_df

    def _transform_metrics(self, series_list: List[TimeSeries]) -> pd.DataFrame:
        """Aggregate each TimeSeries into windowed rolling statistics."""
        feature_frames: Dict[str, pd.Series] = {}

        for ts in series_list:
            if not ts.samples:
                continue

            label_suffix = "_".join(
                f"{v}" for v in ts.labels.values()
            ) if ts.labels else ""
            col_prefix = f"{ts.metric_name}__{label_suffix}" if label_suffix else ts.metric_name

            raw = self._raw_series(ts)
            raw.name = col_prefix

            stats = self._compute_rolling_stats(raw)
            for stat_name, stat_series in stats.items():
                feature_frames[f"{col_prefix}__{stat_name}"] = stat_series

        if not feature_frames:
            return pd.DataFrame()

        df = pd.DataFrame(feature_frames)
        df = df.resample(f"{self.step}s").mean().dropna(how="all").ffill()
        return df

    def _compute_rolling_stats(self, series: pd.Series) -> Dict[str, pd.Series]:
        """Compute windowed statistics for a single metric series."""
        window = f"{self.window_size}s"
        rolling = series.rolling(window, min_periods=1)
        return {
            "mean": rolling.mean(),
            "std": rolling.std().fillna(0),
            "roc": series.diff().fillna(0),
            "p95": rolling.quantile(0.95),
        }

    def _zscore_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Drop rows where *any* column exceeds ``|z| >= zscore_threshold``.

        Runs at the end of ``transform`` (entity-keyed and wide), **before**
        IsolationForest. This is a blunt pre-IF sensor gate: on a quiet
        window a real ``error_*`` spike is itself a high-z event and is
        discarded unless the threshold is loose enough. Prefer raising the
        threshold over relying on IF to see anomalies that never reach it.
        """
        if df.empty:
            return df

        means = df.mean()
        stds = df.std().replace(0, 1)
        zscores = ((df - means) / stds).abs()
        mask = (zscores < self.zscore_threshold).all(axis=1)
        removed = (~mask).sum()
        if removed:
            logger.info("Z-score filter removed %d / %d rows (threshold=%.1f)",
                        removed, len(df), self.zscore_threshold)
        filtered = df.loc[mask]
        # MODIFIED: wide matrices with few rows can lose every row; keep unfiltered set
        if filtered.empty and not df.empty:
            logger.warning(
                "Z-score filter removed all %d rows; keeping unfiltered matrix for training",
                len(df),
            )
            return df
        return filtered

    def _extract_log_features(self, log_streams: List[LogStream]) -> pd.DataFrame:
        """
        Derive time-bucketed features from log streams:
        error_rate, log_volume, unique_patterns.
        """
        if not log_streams:
            return pd.DataFrame()

        records: List[Dict] = []
        for stream in log_streams:
            for entry in stream.entries:
                records.append({
                    "timestamp": pd.Timestamp.utcfromtimestamp(entry.timestamp),
                    "level": entry.level or "UNKNOWN",
                    "message": entry.message,
                })

        if not records:
            return pd.DataFrame()

        log_df = pd.DataFrame(records).set_index("timestamp").sort_index()
        bucketed = log_df.resample(f"{self.step}s")

        features = pd.DataFrame(index=bucketed.indices.keys())
        features["log_volume"] = bucketed.size()
        features["error_count"] = bucketed.apply(
            lambda g: (g["level"].isin(["ERROR", "FATAL", "CRITICAL", "PANIC"])).sum()
            if not g.empty else 0
        )
        features["error_rate"] = (
            features["error_count"] / features["log_volume"].replace(0, 1)
        )
        features["unique_patterns"] = bucketed["message"].apply(
            lambda msgs: len(set(msgs)) if not msgs.empty else 0
        )

        return features
