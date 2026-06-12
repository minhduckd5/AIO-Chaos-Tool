"""Shared telemetry → anomaly analysis pipeline."""

from __future__ import annotations

from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.schemas.scenarios import AnomalyCluster, AnomalySummary
from chaosgen.schemas.telemetry import TelemetryDataset


def analyze_dataset(
    dataset: TelemetryDataset,
) -> tuple[list[AnomalyCluster], list[AnomalySummary], int]:
    """
    Transform telemetry, fit IsolationForest, and return anomaly summaries.

    Returns (clusters, summaries, feature_row_count).
    """
    fe = FeatureEngineer()
    features = fe.transform(dataset)
    if features.empty:
        return [], [], 0

    detector = AnomalyDetector()
    detector.fit(features)
    clusters, summaries = detector.detect_and_summarize(features)
    return clusters, summaries, len(features)
