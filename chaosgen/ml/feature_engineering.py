import logging
from collections import Counter
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from chaosgen.schemas.telemetry import TelemetryDataset, TimeSeries, LogStream

logger = logging.getLogger(__name__)


class FeatureEngineer:
    """
    Transforms raw TelemetryDataset into a feature matrix suitable
    for unsupervised anomaly detection.

    Pipeline: raw samples -> windowed aggregation -> rolling stats ->
              z-score filtering -> merged feature DataFrame.
    """

    def __init__(
        self,
        window_size: int = 300,
        step: int = 60,
        zscore_threshold: float = 3.0,
        settings: Optional[Any] = None,
    ):
        """
        Args:
            window_size: Aggregation window in seconds.
            step: Slide step in seconds.
            zscore_threshold: Outlier removal threshold applied per-feature
                              before ML training (sensor-level noise removal).
        """
        if settings is not None:
            self.window_size = getattr(settings, "rolling_window_seconds", window_size)
            self.step = getattr(settings, "resample_step_seconds", step)
            self.zscore_threshold = getattr(settings, "zscore_threshold", zscore_threshold)
        else:
            self.window_size = window_size
            self.step = step
            self.zscore_threshold = zscore_threshold

    def transform(self, dataset: TelemetryDataset) -> pd.DataFrame:
        """Convert a full TelemetryDataset into a feature matrix."""
        metric_df = self._transform_metrics(dataset.metrics)
        log_df = self._extract_log_features(dataset.logs)

        if metric_df.empty:
            logger.warning("No metric features extracted; returning empty DataFrame.")
            return pd.DataFrame()

        if not log_df.empty:
            metric_df = metric_df.join(log_df, how="left").fillna(0)

        metric_df = self._zscore_filter(metric_df)
        logger.info("Feature matrix shape: %s", metric_df.shape)
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

            idx = pd.to_datetime(ts.timestamps, unit="s", utc=True)
            raw = pd.Series(ts.values, index=idx, name=col_prefix, dtype=float)
            raw = raw.sort_index()
            if raw.index.has_duplicates:
                raw = raw[~raw.index.duplicated(keep="last")]

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
        """Remove rows where any feature exceeds the z-score threshold."""
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
