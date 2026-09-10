"""Shared telemetry → anomaly analysis pipeline."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Callable

from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.canonical_features import apply_canonical_features
from chaosgen.ml.cluster_labels import ClusterLabelStore
from chaosgen.ml.feature_engineering import FeatureEngineer, FeatureLayoutMismatchError
from chaosgen.schemas.scenarios import AnomalyCluster, AnomalySummary
from chaosgen.schemas.telemetry import TelemetryDataset

if TYPE_CHECKING:
    from chaosgen.config.settings import ChaosGenSettings

logger = logging.getLogger(__name__)


def analyze_dataset(
    dataset: TelemetryDataset,
    model_path: str | None = None,
    settings: ChaosGenSettings | None = None,
    notice_cb: Callable[[str], None] | None = None,
) -> tuple[list[AnomalyCluster], list[AnomalySummary], int]:
    """
    Transform telemetry, fit IsolationForest (or load pre-trained model), and return anomaly summaries.

    ``notice_cb`` receives operator-facing notices (e.g. an incompatible
    pre-trained model triggering a refit) so CLI/GUI can surface them without a
    raw traceback.

    Returns (clusters, summaries, feature_row_count).
    """
    feature_settings = settings.features if settings is not None else None
    fe = FeatureEngineer(settings=feature_settings)
    features = fe.transform(dataset)
    if feature_settings is not None:
        features = apply_canonical_features(features, feature_settings)
    if features.empty:
        return [], [], 0

    anomaly_settings = settings.anomaly if settings is not None else None
    detector = AnomalyDetector(settings=anomaly_settings)
    # MODIFIED: P0-A — fall back to settings.anomaly.default_model_path
    resolved_model = model_path
    if not resolved_model and settings is not None:
        resolved_model = settings.anomaly.default_model_path

    label_store = None
    if resolved_model and os.path.exists(resolved_model):
        # --- START MODIFICATION ---
        # Incompatible layout must never zero-align silently, and must never
        # surface as a traceback mid-demo: warn once and refit on this window.
        try:
            detector.load_model(resolved_model, expected_layout=fe.layout)
            label_store = ClusterLabelStore.sidecar_for_model(resolved_model)
            if label_store.path.exists():
                label_store.load()
        except FeatureLayoutMismatchError as exc:
            logger.warning("%s", exc)
            if notice_cb is not None:
                notice_cb(str(exc))
            detector = AnomalyDetector(settings=anomaly_settings)
            detector.fit(features)
            label_store = None
        # --- END MODIFICATION ---
    else:
        detector.fit(features)

    clusters, summaries = detector.detect_and_summarize(features)
    if label_store is not None:
        summaries = label_store.annotate_summaries(summaries)
    return clusters, summaries, len(features)
