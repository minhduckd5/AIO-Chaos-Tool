import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from chaosgen.schemas.scenarios import AnomalyCluster, AnomalySeverity, AnomalySummary

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """
    Two-stage unsupervised anomaly detection:
      1. IsolationForest identifies anomalous time windows.
      2. KMeans clusters the anomalies by behavioral similarity.

    Outputs List[AnomalyCluster] consumed by LLMAdvisor.
    """

    def __init__(
        self,
        contamination: float = 0.1,
        n_clusters: int = 5,
        random_state: int = 42,
    ):
        self.contamination = contamination
        self.n_clusters = n_clusters
        self.random_state = random_state

        self.scaler = StandardScaler()
        self.iso_forest = IsolationForest(
            contamination=contamination,
            random_state=random_state,
            n_jobs=-1,
        )
        self.kmeans: Optional[KMeans] = None
        self._is_fitted = False
        self._feature_names: List[str] = []

    def fit(self, features: pd.DataFrame) -> "AnomalyDetector":
        """Train IsolationForest on baseline feature matrix."""
        if features.empty:
            raise ValueError("Cannot fit on empty feature matrix.")

        self._feature_names = list(features.columns)
        scaled = self.scaler.fit_transform(features.values)
        self.iso_forest.fit(scaled)
        self._is_fitted = True
        logger.info("IsolationForest fitted on %d samples, %d features",
                     scaled.shape[0], scaled.shape[1])
        return self

    def detect(self, features: pd.DataFrame) -> List[AnomalyCluster]:
        """
        Detect anomalies and cluster them by behavioral similarity.
        Returns a list of AnomalyCluster objects.
        """
        if not self._is_fitted:
            raise RuntimeError("AnomalyDetector must be fitted before detection.")

        scaled = self.scaler.transform(features.values)
        labels = self.iso_forest.predict(scaled)
        scores = self.iso_forest.decision_function(scaled)

        anomaly_mask = labels == -1
        anomaly_count = anomaly_mask.sum()
        logger.info("Detected %d anomalous windows out of %d total",
                     anomaly_count, len(features))

        if anomaly_count == 0:
            return []

        anomaly_features = scaled[anomaly_mask]
        anomaly_scores = scores[anomaly_mask]
        anomaly_indices = features.index[anomaly_mask]

        actual_k = min(self.n_clusters, anomaly_count)
        self.kmeans = KMeans(n_clusters=actual_k, random_state=self.random_state, n_init=10)
        cluster_labels = self.kmeans.fit_predict(anomaly_features)

        return self._build_clusters(
            anomaly_features, anomaly_scores, anomaly_indices,
            cluster_labels, features.columns.tolist(),
        )

    def detect_and_summarize(
        self,
        features: pd.DataFrame,
        service_resolver: Optional[Dict[str, str]] = None,
    ) -> Tuple[List[AnomalyCluster], List[AnomalySummary]]:
        """
        Detect anomalies and produce compact AnomalySummary objects
        ready for LLM consumption.
        """
        clusters = self.detect(features)
        summaries = [
            self._cluster_to_summary(c, service_resolver) for c in clusters
        ]
        return clusters, summaries

    def save_model(self, path: str) -> None:
        state = {
            "scaler": self.scaler,
            "iso_forest": self.iso_forest,
            "kmeans": self.kmeans,
            "feature_names": self._feature_names,
            "contamination": self.contamination,
            "n_clusters": self.n_clusters,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(state, path)
        logger.info("Model saved to %s", path)

    def load_model(self, path: str) -> "AnomalyDetector":
        state = joblib.load(path)
        self.scaler = state["scaler"]
        self.iso_forest = state["iso_forest"]
        self.kmeans = state["kmeans"]
        self._feature_names = state["feature_names"]
        self.contamination = state["contamination"]
        self.n_clusters = state["n_clusters"]
        self._is_fitted = True
        logger.info("Model loaded from %s", path)
        return self

    def _build_clusters(
        self,
        anomaly_features: np.ndarray,
        anomaly_scores: np.ndarray,
        anomaly_indices: pd.DatetimeIndex,
        cluster_labels: np.ndarray,
        feature_names: List[str],
    ) -> List[AnomalyCluster]:
        clusters: List[AnomalyCluster] = []

        for cid in range(int(cluster_labels.max()) + 1):
            mask = cluster_labels == cid
            cluster_features = anomaly_features[mask]
            cluster_scores = anomaly_scores[mask]
            cluster_timestamps = anomaly_indices[mask]

            mean_score = float(np.mean(cluster_scores))
            severity = self._score_to_severity(mean_score)

            dominant = self._top_deviating_features(
                cluster_features, feature_names, top_n=5
            )

            affected = self._extract_service_names(dominant)

            ts_list = [float(t.timestamp()) for t in cluster_timestamps] if hasattr(
                cluster_timestamps[0], "timestamp"
            ) else cluster_timestamps.tolist()

            centroid = self.kmeans.cluster_centers_[cid].tolist() if self.kmeans else None

            clusters.append(AnomalyCluster(
                cluster_id=cid,
                severity=severity,
                affected_services=affected,
                dominant_features=dominant,
                sample_count=int(mask.sum()),
                sample_timestamps=ts_list,
                centroid=centroid,
            ))

        return clusters

    @staticmethod
    def _score_to_severity(iso_score: float) -> AnomalySeverity:
        """Map IsolationForest decision score to severity tier.
        More negative = more anomalous."""
        if iso_score < -0.3:
            return AnomalySeverity.CRITICAL
        elif iso_score < -0.15:
            return AnomalySeverity.HIGH
        elif iso_score < -0.05:
            return AnomalySeverity.MEDIUM
        return AnomalySeverity.LOW

    @staticmethod
    def _top_deviating_features(
        cluster_features: np.ndarray,
        feature_names: List[str],
        top_n: int = 5,
    ) -> List[Tuple[str, float]]:
        """Identify features with highest mean absolute deviation in this cluster."""
        mean_abs = np.mean(np.abs(cluster_features), axis=0)
        top_indices = np.argsort(mean_abs)[-top_n:][::-1]
        return [
            (feature_names[i], round(float(mean_abs[i]), 3))
            for i in top_indices
            if i < len(feature_names)
        ]

    @staticmethod
    def _extract_service_names(
        dominant_features: List[Tuple[str, float]],
    ) -> List[str]:
        """Heuristic: extract service names from feature column names.
        Convention: metric_name__service_name__stat"""
        services = set()
        for feat_name, _ in dominant_features:
            parts = feat_name.split("__")
            if len(parts) >= 2:
                services.add(parts[1])
        return sorted(services) if services else ["unknown"]

    @staticmethod
    def _cluster_to_summary(
        cluster: AnomalyCluster,
        service_resolver: Optional[Dict[str, str]] = None,
    ) -> AnomalySummary:
        """Compress an AnomalyCluster into an LLM-consumable AnomalySummary."""
        service = cluster.affected_services[0] if cluster.affected_services else "unknown"
        if service_resolver and service in service_resolver:
            service = service_resolver[service]

        severity_map = {
            AnomalySeverity.LOW: 0.25,
            AnomalySeverity.MEDIUM: 0.50,
            AnomalySeverity.HIGH: 0.75,
            AnomalySeverity.CRITICAL: 1.0,
        }

        top3 = cluster.dominant_features[:3]

        error_pattern = AnomalyDetector._derive_error_pattern(cluster)

        if cluster.sample_timestamps:
            start_ts = min(cluster.sample_timestamps)
            end_ts = max(cluster.sample_timestamps)
            start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
            end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
            time_window = f"{start_dt:%Y-%m-%d %H:%M} - {end_dt:%H:%M} UTC"
        else:
            time_window = "unknown"

        return AnomalySummary(
            service_name=service,
            top_features=top3,
            error_pattern=error_pattern,
            severity=severity_map.get(cluster.severity, 0.5),
            time_window=time_window,
            source_cluster_id=cluster.cluster_id,
        )

    @staticmethod
    def _derive_error_pattern(cluster: AnomalyCluster) -> Optional[str]:
        """
        Derive a log error pattern from dominant features.

        The log-derived columns `error_rate` / `error_count` count ERROR/FATAL/
        CRITICAL/PANIC log lines (see FeatureEngineer._extract_log_features), so a
        dominating error feature is treated as a severe log signal. Returns a
        string containing a severe keyword (consumed by the gatekeeper), or None.
        """
        for feat_name, score in cluster.dominant_features:
            lowered = feat_name.lower()
            if "error_rate" in lowered or "error_count" in lowered:
                return f"error: elevated {feat_name} (z={score:.2f})"
        return None
