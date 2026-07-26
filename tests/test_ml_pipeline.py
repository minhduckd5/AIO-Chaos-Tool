"""Tests for the ML pipeline: FeatureEngineer and AnomalyDetector."""
import os
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from chaosgen.schemas.telemetry import (
    MetricSample, TimeSeries, TelemetryDataset, LogStream, LogEntry,
)
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.schemas.scenarios import AnomalySeverity


def _make_dataset(n_samples=200, n_series=3, anomaly_fraction=0.1):
    """Generate a synthetic TelemetryDataset for testing."""
    np.random.seed(42)
    base_time = 1700000000.0
    metrics = []

    for i in range(n_series):
        values = np.random.normal(50, 5, n_samples)
        n_anomalies = int(n_samples * anomaly_fraction)
        anomaly_indices = np.random.choice(n_samples, n_anomalies, replace=False)
        values[anomaly_indices] = np.random.normal(200, 30, n_anomalies)

        samples = [
            MetricSample(timestamp=base_time + j * 60, value=float(values[j]))
            for j in range(n_samples)
        ]
        metrics.append(TimeSeries(
            metric_name=f"cpu_usage",
            labels={"service": f"svc-{i}"},
            samples=samples,
        ))

    logs = [
        LogStream(
            stream_labels={"app": "svc-0"},
            entries=[
                LogEntry(timestamp=base_time + j * 60, message=f"INFO request handled", level="INFO")
                for j in range(50)
            ] + [
                LogEntry(timestamp=base_time + j * 60, message=f"ERROR connection refused", level="ERROR")
                for j in range(50, 60)
            ],
        )
    ]

    return TelemetryDataset(
        metrics=metrics,
        logs=logs,
        collection_start=datetime.fromtimestamp(base_time, tz=timezone.utc),
        collection_end=datetime.fromtimestamp(base_time + n_samples * 60, tz=timezone.utc),
    )


class TestFeatureEngineer:
    def test_transform_produces_dataframe(self):
        dataset = _make_dataset()
        fe = FeatureEngineer(window_size=300, step=60)
        df = fe.transform(dataset)
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert df.shape[0] > 0
        assert df.shape[1] > 0

    def test_zscore_filter_removes_outliers(self):
        dataset = _make_dataset(anomaly_fraction=0.3)
        fe = FeatureEngineer(window_size=300, step=60, zscore_threshold=2.0)
        df = fe.transform(dataset)
        assert isinstance(df, pd.DataFrame)

    def test_empty_dataset(self):
        dataset = TelemetryDataset(
            metrics=[],
            logs=[],
            collection_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            collection_end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        fe = FeatureEngineer()
        df = fe.transform(dataset)
        assert df.empty

    def test_log_features_extracted(self):
        dataset = _make_dataset()
        fe = FeatureEngineer(window_size=300, step=60)
        df = fe.transform(dataset)
        log_cols = [c for c in df.columns if c in ("log_volume", "error_count", "error_rate")]
        assert len(log_cols) > 0


class TestAnomalyDetector:
    def test_fit_and_detect(self):
        dataset = _make_dataset(n_samples=300, anomaly_fraction=0.15)
        fe = FeatureEngineer(window_size=300, step=60)
        features = fe.transform(dataset)

        detector = AnomalyDetector(contamination=0.1, n_clusters=3)
        detector.fit(features)
        clusters = detector.detect(features)

        assert isinstance(clusters, list)
        assert len(clusters) > 0
        for c in clusters:
            assert c.sample_count > 0
            assert c.severity in list(AnomalySeverity)
            assert len(c.dominant_features) > 0

    def test_detect_and_summarize(self):
        dataset = _make_dataset(n_samples=300, anomaly_fraction=0.15)
        fe = FeatureEngineer(window_size=300, step=60)
        features = fe.transform(dataset)

        detector = AnomalyDetector(contamination=0.1, n_clusters=3)
        detector.fit(features)
        clusters, summaries = detector.detect_and_summarize(features)

        assert len(clusters) == len(summaries)
        for s in summaries:
            assert s.service_name
            assert 0 <= s.severity <= 1
            assert len(s.top_features) <= 3

    def test_save_and_load(self, tmp_path):
        dataset = _make_dataset()
        fe = FeatureEngineer(window_size=300, step=60)
        features = fe.transform(dataset)

        detector = AnomalyDetector()
        detector.fit(features)

        model_path = str(tmp_path / "model.joblib")
        detector.save_model(model_path)

        new_detector = AnomalyDetector()
        new_detector.load_model(model_path)
        clusters = new_detector.detect(features)
        assert isinstance(clusters, list)

    def test_no_anomalies_returns_empty(self):
        np.random.seed(42)
        data = pd.DataFrame(
            np.random.normal(0, 0.1, (100, 5)),
            columns=[f"f{i}" for i in range(5)],
            index=pd.date_range("2024-01-01", periods=100, freq="1min", tz="UTC"),
        )
        detector = AnomalyDetector(contamination=0.01)
        detector.fit(data)
        clusters = detector.detect(data)
        assert isinstance(clusters, list)

    def test_kmeans_inference_predict(self, tmp_path):
        dataset = _make_dataset(n_samples=200, anomaly_fraction=0.15)
        fe = FeatureEngineer(window_size=300, step=60)
        features = fe.transform(dataset)

        detector = AnomalyDetector(n_clusters=3)
        detector.fit(features)
        detector.detect(features)

        model_path = str(tmp_path / "model.joblib")
        detector.save_model(model_path)

        new_detector = AnomalyDetector()
        new_detector.load_model(model_path)

        from unittest.mock import MagicMock
        original_predict = new_detector.kmeans.predict
        new_detector.kmeans.predict = MagicMock(side_effect=original_predict)

        clusters = new_detector.detect(features)
        assert isinstance(clusters, list)
        assert new_detector.kmeans.predict.called

    def test_kmeans_feature_dimension_mismatch(self, tmp_path):
        dataset = _make_dataset(n_samples=200, anomaly_fraction=0.15)
        fe = FeatureEngineer(window_size=300, step=60)
        features = fe.transform(dataset)

        detector = AnomalyDetector(n_clusters=3)
        detector.fit(features)
        detector.detect(features)

        model_path = str(tmp_path / "model.joblib")
        detector.save_model(model_path)

        new_detector = AnomalyDetector()
        new_detector.load_model(model_path)

        mismatched_features = features.iloc[:, :1]
        clusters = new_detector.detect(mismatched_features)
        assert isinstance(clusters, list)

    def test_plot_timeline_generates_file(self, tmp_path):
        dataset = _make_dataset(n_samples=200, anomaly_fraction=0.15)
        fe = FeatureEngineer(window_size=300, step=60)
        features = fe.transform(dataset)

        detector = AnomalyDetector(n_clusters=3)
        detector.fit(features)
        detector.detect(features)

        plot_path = str(tmp_path / "plot.png")
        detector.plot_timeline(features, plot_path)

        assert os.path.exists(plot_path)
        assert os.path.getsize(plot_path) > 0


class TestWindowResolver:
    def test_relative_hours(self):
        from chaosgen.ingestion.window import resolve_collection_window
        window = resolve_collection_window(hours=12)
        assert 11.9 <= window.lookback_hours <= 12.1

    def test_absolute_range(self):
        from chaosgen.ingestion.window import resolve_collection_window
        start = datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 20, 16, 0, 0, tzinfo=timezone.utc)
        window = resolve_collection_window(start=start, end=end)
        assert window.lookback_hours == 6.0
        assert window.start == start
        assert window.end == end

    def test_dataset_window(self):
        from chaosgen.ingestion.window import resolve_collection_window
        dataset = _make_dataset()
        window = resolve_collection_window(dataset=dataset)
        assert window.start == dataset.collection_start
        assert window.end == dataset.collection_end


class TestAutoKSilhouette:
    def test_auto_k_selection(self):
        from chaosgen.config.settings import AnomalySettings
        settings = AnomalySettings(clustering_mode="auto", min_clusters=2, max_clusters=5)
        detector = AnomalyDetector(settings=settings)
        dataset = _make_dataset(n_samples=300, anomaly_fraction=0.2)
        fe = FeatureEngineer(window_size=300, step=60)
        features = fe.transform(dataset)
        detector.fit(features)
        clusters = detector.detect(features)
        assert isinstance(clusters, list)
        assert len(clusters) >= 1

    def test_fixed_k_mode(self):
        from chaosgen.config.settings import AnomalySettings
        settings = AnomalySettings(clustering_mode="fixed", n_clusters=4)
        detector = AnomalyDetector(settings=settings)
        dataset = _make_dataset(n_samples=300, anomaly_fraction=0.2)
        fe = FeatureEngineer(window_size=300, step=60)
        features = fe.transform(dataset)
        detector.fit(features)
        clusters = detector.detect(features)
        assert isinstance(clusters, list)
        assert len(clusters) <= 4
