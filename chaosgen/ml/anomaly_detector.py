import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from chaosgen.ml.canonical_features import align_features_to_model
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
        clustering_mode: str = "auto",
        min_clusters: int = 2,
        max_clusters: int = 15,
        settings: Optional[Any] = None,
    ):
        if settings is not None:
            self.contamination = getattr(settings, "contamination", contamination)
            self.n_clusters = getattr(settings, "n_clusters", n_clusters)
            self.clustering_mode = getattr(settings, "clustering_mode", clustering_mode)
            self.min_clusters = getattr(settings, "min_clusters", min_clusters)
            self.max_clusters = getattr(settings, "max_clusters", max_clusters)
        else:
            self.contamination = contamination
            self.n_clusters = n_clusters
            self.clustering_mode = clustering_mode
            self.min_clusters = min_clusters
            self.max_clusters = max_clusters

        self.random_state = random_state
        self.scaler = StandardScaler()
        self.iso_forest = IsolationForest(
            contamination=self.contamination,
            random_state=random_state,
            n_jobs=-1,
        )
        self.kmeans: Optional[KMeans] = None
        self._is_fitted = False
        self._feature_names: List[str] = []
        self.canonical_schema_version: Optional[str] = None
        # MODIFIED: P7/P0 — last K chosen during detect (auto or fixed)
        self.last_chosen_k: Optional[int] = None

    def _choose_k_silhouette(self, anomaly_features: np.ndarray) -> int:
        """Choose optimal KMeans cluster count k using Silhouette Score on anomalous features."""
        from sklearn.metrics import silhouette_score

        n_samples = len(anomaly_features)
        if n_samples <= 1:
            return 1
        if n_samples == 2:
            return 2

        min_k = max(2, min(self.min_clusters, n_samples))
        max_k = min(self.max_clusters, n_samples - 1)

        if max_k < min_k:
            return min_k

        best_k = min_k
        best_score = -1.0

        for k in range(min_k, max_k + 1):
            try:
                km = KMeans(n_clusters=k, random_state=self.random_state, n_init=10)
                labels = km.fit_predict(anomaly_features)
                if len(set(labels)) < 2:
                    continue
                score = float(silhouette_score(anomaly_features, labels))
                if score > best_score:
                    best_score = score
                    best_k = k
            except Exception as e:
                logger.debug("Silhouette calculation failed for k=%d: %s", k, e)
                continue

        logger.info("Auto-K Silhouette selected k=%d (best_score=%.3f, anomaly_windows=%d)", best_k, best_score, n_samples)
        return best_k

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

    def _align_for_inference(self, features: pd.DataFrame) -> pd.DataFrame:
        """Reindex live features to the trained schema before scaler/IF."""
        if self._feature_names:
            return align_features_to_model(features, self._feature_names)
        return features

    def detect(self, features: pd.DataFrame) -> List[AnomalyCluster]:
        """
        Identify anomalies in *features* using fitted IsolationForest,
        then cluster anomalous windows using KMeans.
        """
        if self._is_fitted and self._feature_names:
            features = self._align_for_inference(features)

        expected_scaler_features = getattr(self.scaler, "n_features_in_", None)
        if not self._is_fitted or (expected_scaler_features is not None and features.shape[1] != expected_scaler_features):
            if self._is_fitted:
                logger.warning(
                    "Feature count mismatch: model expects %d features, but got %d. Falling back to dynamic refit.",
                    expected_scaler_features, features.shape[1]
                )
            self.scaler = StandardScaler()
            scaled = self.scaler.fit_transform(features.values)
            self.iso_forest = IsolationForest(
                contamination=self.contamination,
                random_state=self.random_state,
                n_jobs=-1,
            )
            self.iso_forest.fit(scaled)
            labels = self.iso_forest.predict(scaled)
            scores = self.iso_forest.decision_function(scaled)
            self.kmeans = None  # Reset KMeans to force dynamic fit on the new scaled features
        else:
            scaled = self.scaler.transform(features.values)
            labels = self.iso_forest.predict(scaled)
            scores = self.iso_forest.decision_function(scaled)

        anomaly_mask = labels == -1
        anomaly_count = anomaly_mask.sum()
        logger.info("Detected %d anomalous windows out of %d total",
                     anomaly_count, len(features))

        if anomaly_count == 0:
            self.last_chosen_k = 0
            return []

        anomaly_features = scaled[anomaly_mask]
        anomaly_scores = scores[anomaly_mask]
        anomaly_indices = features.index[anomaly_mask]

        # Check if pre-trained KMeans model is already loaded
        if self.kmeans is not None:
            expected_features = getattr(self.kmeans, "n_features_in_", None)
            if expected_features is not None and anomaly_features.shape[1] != expected_features:
                logger.warning(
                    "Feature dimension mismatch: model expects %d, got %d. Falling back to dynamic fit.",
                    expected_features, anomaly_features.shape[1]
                )
                if self.clustering_mode == "fixed":
                    actual_k = min(self.n_clusters, anomaly_count)
                else:
                    actual_k = self._choose_k_silhouette(anomaly_features)

                if anomaly_count == 1:
                    cluster_labels = np.zeros(1, dtype=int)
                    actual_k = 1
                else:
                    self.kmeans = KMeans(n_clusters=actual_k, random_state=self.random_state, n_init=10)
                    cluster_labels = self.kmeans.fit_predict(anomaly_features)
                self.last_chosen_k = int(actual_k)
            else:
                cluster_labels = self.kmeans.predict(anomaly_features)
                self.last_chosen_k = int(getattr(self.kmeans, "n_clusters", len(set(cluster_labels))))
        else:
            # Dynamic Fit Mode
            if self.clustering_mode == "fixed":
                actual_k = min(self.n_clusters, anomaly_count)
            else:
                actual_k = self._choose_k_silhouette(anomaly_features)

            if anomaly_count == 1:
                cluster_labels = np.zeros(1, dtype=int)
                actual_k = 1
            else:
                self.kmeans = KMeans(n_clusters=actual_k, random_state=self.random_state, n_init=10)
                cluster_labels = self.kmeans.fit_predict(anomaly_features)
            self.last_chosen_k = int(actual_k)

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
            "canonical_schema_version": self.canonical_schema_version,
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
        self.canonical_schema_version = state.get("canonical_schema_version")
        self._is_fitted = True
        logger.info("Model loaded from %s", path)
        return self

    def get_timeline_data(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        Computes the normalized anomaly scores and assigns KMeans cluster labels
        to anomalous windows. Returns a DataFrame with columns ['score', 'anomaly_cluster'].
        """
        if not self._is_fitted:
            raise RuntimeError("AnomalyDetector must be fitted before scoring.")

        if self._feature_names:
            features = self._align_for_inference(features)

        expected_features = getattr(self.scaler, "n_features_in_", None)
        if expected_features is not None and features.shape[1] != expected_features:
            logger.warning(
                "Feature count mismatch: model expects %d features, but got %d. "
                "Falling back to dynamic fit (unsupervised mode).",
                expected_features, features.shape[1]
            )
            scaler = StandardScaler()
            scaled = scaler.fit_transform(features.values)
            iso_forest = IsolationForest(
                contamination=self.contamination,
                random_state=self.random_state,
                n_jobs=-1,
            )
            iso_forest.fit(scaled)
            scores = iso_forest.decision_function(scaled)
            labels = iso_forest.predict(scaled)
        else:
            scaled = self.scaler.transform(features.values)
            scores = self.iso_forest.decision_function(scaled)
            labels = self.iso_forest.predict(scaled)

        anomaly_mask = labels == -1
        anomaly_count = anomaly_mask.sum()

        raw_scores = -scores
        min_score = raw_scores.min()
        max_score = raw_scores.max()
        if max_score > min_score:
            normalized_scores = (raw_scores - min_score) / (max_score - min_score)
        else:
            normalized_scores = np.zeros_like(raw_scores)

        timeline = pd.DataFrame(index=features.index)
        timeline['score'] = normalized_scores
        timeline['anomaly_cluster'] = -1

        if anomaly_count > 0:
            anomaly_features = scaled[anomaly_mask]
            if self.kmeans is not None and getattr(self.kmeans, "n_features_in_", None) == anomaly_features.shape[1]:
                cluster_labels = self.kmeans.predict(anomaly_features)
            else:
                actual_k = min(self.n_clusters, anomaly_count)
                temp_kmeans = KMeans(n_clusters=actual_k, random_state=self.random_state, n_init=10)
                cluster_labels = temp_kmeans.fit_predict(anomaly_features)
            
            timeline.loc[anomaly_mask, 'anomaly_cluster'] = cluster_labels

        return timeline

    def plot_timeline(self, features: pd.DataFrame, output_path: str) -> None:
        """
        Generates a timeline plot showing the system anomaly score over time,
        highlighting anomaly points colored by their KMeans Cluster ID.
        """
        import os
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        timeline = self.get_timeline_data(features)
        anomaly_count = (timeline['anomaly_cluster'] != -1).sum()

        # Build cluster summaries for legend
        cluster_info_map = {}
        if anomaly_count > 0:
            try:
                clusters, _ = self.detect_and_summarize(features)
                cluster_info_map = {c.cluster_id: c for c in clusters}
            except Exception as e:
                logger.warning("Could not build cluster summaries for legend: %s", e)

        # Generate the plot
        plt.figure(figsize=(14, 7))
        plt.plot(timeline.index, timeline['score'], color='#BDC3C7', label='Outlier Score (Trace)', zorder=1, linewidth=1.5)

        # Color palette for clusters: premium, vibrant colors
        colors = ['#FF5E5B', '#00ADFF', '#00E676', '#FFA500', '#D500F9', '#FFD700', '#00CED1', '#FF1493', '#9B59B6', '#1ABC9C']

        # Plot normal points
        normal_mask = timeline['anomaly_cluster'] == -1
        plt.scatter(timeline.index[normal_mask], timeline.loc[normal_mask, 'score'], 
                    color='#7F8C8D', alpha=0.3, s=15, label='Normal State', zorder=2)

        # Plot each cluster separately to show them in the legend
        cluster_labels = timeline.loc[~normal_mask, 'anomaly_cluster'].values
        unique_clusters = sorted(list(set(cluster_labels))) if anomaly_count > 0 else []
        for cid in unique_clusters:
            cid_mask = timeline['anomaly_cluster'] == cid
            cluster_color = colors[cid % len(colors)]
            
            info = cluster_info_map.get(cid)
            if info:
                # Clean up feature names to make the legend readable (e.g. drop prefixes/suffixes)
                cleaned_feats = []
                for f, _ in info.dominant_features[:2]:
                    parts = f.split("__")
                    cleaned_feats.append(parts[1] if len(parts) >= 2 else f)
                top_feats = ", ".join(cleaned_feats)
                label_text = f"Cluster {cid} ({info.sample_count} windows): {top_feats}"
            else:
                label_text = f"Cluster {cid}"
                
            plt.scatter(timeline.index[cid_mask], timeline.loc[cid_mask, 'score'],
                        color=cluster_color, s=50, label=label_text, zorder=3, edgecolors='black', linewidths=0.5)

        # Set title, labels, formatting
        plt.title('System Anomaly Timeline (Color-Coded by Behavioral Cluster)', fontsize=14, fontweight='bold', pad=15)
        plt.xlabel('Timestamp (UTC)', fontsize=12)
        plt.ylabel('Normalized Outlier Score (Higher = More Anomalous)', fontsize=12)
        
        is_datetime = isinstance(features.index, pd.DatetimeIndex) or (
            len(features.index) > 0 and isinstance(features.index[0], (pd.Timestamp, datetime))
        )

        # Format dates nicely if they are DatetimeIndex
        if is_datetime:
            plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
            plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
            plt.gcf().autofmt_xdate()

        plt.grid(True, linestyle='--', alpha=0.5)
        plt.legend(loc='upper left', frameon=True, facecolor='white', edgecolor='#BDC3C7', framealpha=0.9)
        plt.tight_layout()

        # Save plot
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        plt.savefig(output_path, dpi=150)
        plt.close()
        logger.info("Anomaly timeline plot saved to %s", output_path)


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
