"""Shared telemetry → anomaly analysis pipeline."""

from __future__ import annotations

from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.schemas.scenarios import AnomalyCluster, AnomalySummary
from chaosgen.schemas.telemetry import TelemetryDataset


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chaosgen.config.settings import ChaosGenSettings


def analyze_dataset(
    dataset: TelemetryDataset,
    model_path: str | None = None,
    settings: ChaosGenSettings | None = None,
) -> tuple[list[AnomalyCluster], list[AnomalySummary], int]:
    """
    Transform telemetry, fit IsolationForest (or load pre-trained model), and return anomaly summaries.

    Returns (clusters, summaries, feature_row_count).
    """
    feature_settings = settings.features if settings is not None else None
    fe = FeatureEngineer(settings=feature_settings)
    features = fe.transform(dataset)
    if features.empty:
        return [], [], 0

    anomaly_settings = settings.anomaly if settings is not None else None
    detector = AnomalyDetector(settings=anomaly_settings)
    if model_path:
        detector.load_model(model_path)
    else:
        detector.fit(features)
    clusters, summaries = detector.detect_and_summarize(features)
    return clusters, summaries, len(features)
