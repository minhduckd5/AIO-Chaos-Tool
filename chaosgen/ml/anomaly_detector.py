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

from chaosgen.ml.canonical_features import (
    align_features_to_model,
    extract_service_from_column,
    is_concrete_service,
)
from chaosgen.ml.feature_engineering import (
    SERVICE_LEVEL,
    SYSTEM_ENTITY,
    TIMESTAMP_LEVEL,
    WIDE_LAYOUT,
    FeatureLayoutMismatchError,
    detect_feature_layout,
    split_feature_index,
)
from chaosgen.schemas.scenarios import AnomalyCluster, AnomalySeverity, AnomalySummary

logger = logging.getLogger(__name__)

# MODIFIED: entity-keyed columns are service-free (`error_rate__mean`), so the
# error tokens match signal stems instead of per-service column fragments.
_ERROR_FEATURE_TOKENS = (
    "error_rate",
    "error_count",
    "error_ratio",
    "span_error",
)


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
        # MODIFIED: layout of the matrix this detector was fitted/loaded on
        self.feature_layout: Optional[str] = None
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
        self.feature_layout = detect_feature_layout(features)
        scaled = self.scaler.fit_transform(features.values)
        self.iso_forest.fit(scaled)
        self._is_fitted = True
        logger.info("IsolationForest fitted on %d samples, %d features (layout=%s)",
                     scaled.shape[0], scaled.shape[1], self.feature_layout)
        return self

    def _align_for_inference(self, features: pd.DataFrame) -> pd.DataFrame:
        """Reindex live features to the trained schema before scaler/IF."""
        # MODIFIED: never zero-pad across layouts — column names mean different
        # things in wide vs entity-keyed, so alignment would be silently wrong.
        live_layout = detect_feature_layout(features)
        if self.feature_layout and self.feature_layout != live_layout:
            raise FeatureLayoutMismatchError(
                f"Loaded model was trained on feature layout "
                f"'{self.feature_layout}' but the live matrix is '{live_layout}'. "
                "Refit on this window or run 'chaosgen train-model --live'."
            )
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
        # MODIFIED: entity-keyed rows carry the service in the index, so
        # attribution no longer depends on parsing column names.
        timestamp_index, entity_values = split_feature_index(features.index)
        anomaly_indices = timestamp_index[anomaly_mask]
        anomaly_entities = (
            entity_values[anomaly_mask] if entity_values is not None else None
        )

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
            anomaly_entities=anomaly_entities,
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
            # MODIFIED: persist the feature layout so a wide-layout model can
            # never be silently zero-aligned onto entity-keyed features.
            "feature_layout": self.feature_layout or WIDE_LAYOUT,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(state, path)
        logger.info(
            "Model saved to %s (layout=%s, features=%d)",
            path, state["feature_layout"], len(self._feature_names),
        )

    def load_model(
        self, path: str, expected_layout: Optional[str] = None
    ) -> "AnomalyDetector":
        state = joblib.load(path)
        saved_layout = str(state.get("feature_layout") or WIDE_LAYOUT)
        if expected_layout and saved_layout != str(expected_layout):
            raise FeatureLayoutMismatchError(
                f"Model {path} was trained on feature layout '{saved_layout}' "
                f"but this run builds '{expected_layout}' features. "
                "Refitting on the current window; run "
                "'chaosgen train-model --live' to persist a compatible baseline."
            )
        self.scaler = state["scaler"]
        self.iso_forest = state["iso_forest"]
        self.kmeans = state["kmeans"]
        self._feature_names = state["feature_names"]
        self.contamination = state["contamination"]
        self.n_clusters = state["n_clusters"]
        self.canonical_schema_version = state.get("canonical_schema_version")
        self.feature_layout = saved_layout
        self._is_fitted = True
        logger.info("Model loaded from %s (layout=%s)", path, saved_layout)
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

        # MODIFIED: entity-keyed rows collapse to one point per timestamp (worst
        # entity wins) so the GUI/CLI timeline keeps a plain DatetimeIndex.
        timestamp_index, entity_values = split_feature_index(features.index)

        cluster_column = np.full(len(features), -1, dtype=int)
        if anomaly_count > 0:
            anomaly_features = scaled[anomaly_mask]
            if self.kmeans is not None and getattr(self.kmeans, "n_features_in_", None) == anomaly_features.shape[1]:
                cluster_labels = self.kmeans.predict(anomaly_features)
            else:
                actual_k = min(self.n_clusters, anomaly_count)
                temp_kmeans = KMeans(n_clusters=actual_k, random_state=self.random_state, n_init=10)
                cluster_labels = temp_kmeans.fit_predict(anomaly_features)
            cluster_column[anomaly_mask] = np.asarray(cluster_labels, dtype=int)

        timeline = pd.DataFrame(
            {'score': normalized_scores, 'anomaly_cluster': cluster_column},
            index=timestamp_index,
        )
        if entity_values is None:
            return timeline

        timeline[SERVICE_LEVEL] = entity_values
        work = timeline.reset_index(names=TIMESTAMP_LEVEL)
        worst = work.groupby(TIMESTAMP_LEVEL)['score'].idxmax()
        collapsed = work.loc[worst].set_index(TIMESTAMP_LEVEL).sort_index()
        collapsed.index.name = None
        return collapsed

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
                # MODIFIED: entity-keyed names are already short (signal__stat);
                # only long legacy wide names need trimming.
                cleaned_feats = []
                for f, _ in info.dominant_features[:2]:
                    parts = f.split("__")
                    cleaned_feats.append(f if len(parts) <= 2 else parts[1])
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
        
        is_datetime = isinstance(timeline.index, pd.DatetimeIndex) or (
            len(timeline.index) > 0 and isinstance(timeline.index[0], (pd.Timestamp, datetime))
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
        anomaly_entities: Optional[np.ndarray] = None,
    ) -> List[AnomalyCluster]:
        clusters: List[AnomalyCluster] = []
        # MODIFIED: positional take — boolean Index.__getitem__ can yield empty on DTIndex
        index_values = np.asarray(anomaly_indices)

        for cid in sorted({int(x) for x in cluster_labels}):
            mask = np.asarray(cluster_labels == cid)
            if not np.any(mask):
                continue
            cluster_features = anomaly_features[mask]
            cluster_scores = anomaly_scores[mask]
            cluster_timestamps = index_values[mask]
            if cluster_timestamps.size == 0:
                logger.warning("Cluster %d has empty timestamps; skipping", cid)
                continue

            mean_score = float(np.mean(cluster_scores))
            severity = self._score_to_severity(mean_score)

            dominant = self._top_deviating_features(
                cluster_features, feature_names, top_n=5
            )

            cluster_entities = (
                anomaly_entities[mask] if anomaly_entities is not None else None
            )
            affected = self._extract_service_names(dominant, cluster_entities)

            first = cluster_timestamps[0]
            if hasattr(first, "timestamp"):
                ts_list = [float(t.timestamp()) for t in cluster_timestamps]
            elif isinstance(first, (np.datetime64, pd.Timestamp)):
                ts_list = [
                    float(pd.Timestamp(t).timestamp()) for t in cluster_timestamps
                ]
            else:
                ts_list = [float(t) for t in cluster_timestamps]

            centroid = (
                self.kmeans.cluster_centers_[cid].tolist() if self.kmeans else None
            )

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
        cluster_entities: Optional[np.ndarray] = None,
    ) -> List[str]:
        """
        Resolve microservice identities for a cluster.

        Entity-keyed layout: services come from the row index (concrete services
        first, ``_system`` last). Wide layout: parsed from column names.
        """
        # MODIFIED: index-based attribution for the entity-keyed layout
        if cluster_entities is not None and len(cluster_entities):
            observed = {str(e) for e in cluster_entities if str(e)}
            concrete = sorted(e for e in observed if is_concrete_service(e))
            if concrete:
                system = [SYSTEM_ENTITY] if SYSTEM_ENTITY in observed else []
                return concrete + system
            if observed:
                return sorted(observed)

        services: set[str] = set()
        for feat_name, _ in dominant_features:
            service = extract_service_from_column(feat_name)
            if service:
                services.add(service)
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

        Both metric error signals (`error_rate`, `error_ratio`, `span_error_rate`)
        and log-derived ones (`logline_error_rate`, `logline_error_count`, which
        count ERROR/FATAL/CRITICAL/PANIC lines) are treated as severe. The service
        is deliberately absent from the string — it comes from the summary's
        index-derived `service_name`, which keeps `history.db` chronic grouping
        stable across windows.
        """
        for feat_name, score in cluster.dominant_features:
            lowered = feat_name.lower()
            if any(token in lowered for token in _ERROR_FEATURE_TOKENS):
                return f"error: elevated {feat_name} (z={score:.2f})"
        return None
