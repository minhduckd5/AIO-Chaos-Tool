"""Tests for canonical feature mapping (v1 signal-type pooling)."""

from __future__ import annotations

import pandas as pd
import pytest

from chaosgen.config.settings import FeatureSettings
from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.canonical_features import (
    CanonicalFeatureMapper,
    align_features_to_model,
    apply_canonical_features,
)


@pytest.fixture
def mapper() -> CanonicalFeatureMapper:
    return CanonicalFeatureMapper.from_yaml()


def test_canonical_columns_fixed_schema(mapper: CanonicalFeatureMapper):
    assert len(mapper.canonical_columns) == 35
    assert mapper.canonical_columns[0].startswith("canonical__")
    assert "canonical__cpu__mean" in mapper.canonical_columns
    assert "canonical__memory__mean" in mapper.canonical_columns


def test_rcaeval_style_columns(mapper: CanonicalFeatureMapper):
    raw = pd.DataFrame(
        {
            "csv__re3tt__ts-route-service_cpu__mean": [0.5, 0.6],
            "csv__re3tt__ts-route-service_mem__std": [0.1, 0.2],
        },
        index=pd.date_range("2024-01-01", periods=2, freq="60s", tz="UTC"),
    )
    out = mapper.transform(raw)
    assert list(out.columns) == mapper.canonical_columns
    assert out["canonical__cpu__mean"].iloc[0] == pytest.approx(0.5)
    assert out["canonical__memory__std"].iloc[0] == pytest.approx(0.1)


def test_eadro_style_columns(mapper: CanonicalFeatureMapper):
    raw = pd.DataFrame(
        {
            "csv__eadro__ts-order-service__cpu_usage__mean": [0.8],
            "csv__eadro__ts-order-service__memory__p95": [0.9],
        },
        index=pd.date_range("2024-01-01", periods=1, freq="60s", tz="UTC"),
    )
    out = mapper.transform(raw)
    assert out["canonical__cpu__mean"].iloc[0] == pytest.approx(0.8)
    assert out["canonical__memory__p95"].iloc[0] == pytest.approx(0.9)


def test_lab_prom_style_columns(mapper: CanonicalFeatureMapper):
    raw = pd.DataFrame(
        {
            "export__node_cpu__mean": [0.42, 0.55],
            "export__node_memory_MemAvailable__mean": [1.2, 1.3],
            "export__node_network_receive_bytes__std": [0.05, 0.06],
        },
        index=pd.date_range("2024-01-01", periods=2, freq="60s", tz="UTC"),
    )
    out = mapper.transform(raw)
    assert out["canonical__cpu__mean"].max() == pytest.approx(0.55)
    assert out["canonical__memory__mean"].max() == pytest.approx(1.3)
    assert out["canonical__network__std"].max() == pytest.approx(0.06)


def test_log_columns(mapper: CanonicalFeatureMapper):
    raw = pd.DataFrame(
        {
            "log_volume": [100.0, 120.0],
            "error_rate": [0.01, 0.05],
        },
        index=pd.date_range("2024-01-01", periods=2, freq="60s", tz="UTC"),
    )
    out = mapper.transform(raw)
    assert out["canonical__log_volume__mean"].iloc[1] == pytest.approx(120.0)
    assert out["canonical__log_error_rate__mean"].iloc[1] == pytest.approx(0.05)


def test_same_schema_from_different_sources(mapper: CanonicalFeatureMapper):
    rcaeval = pd.DataFrame(
        {"csv__svc_cpu__mean": [1.0]},
        index=pd.date_range("2024-01-01", periods=1, freq="60s", tz="UTC"),
    )
    lab = pd.DataFrame(
        {"export__node_cpu__mean": [2.0]},
        index=pd.date_range("2024-01-01", periods=1, freq="60s", tz="UTC"),
    )
    assert list(mapper.transform(rcaeval).columns) == list(mapper.transform(lab).columns)


def test_apply_canonical_features_respects_settings():
    raw = pd.DataFrame(
        {"export__node_cpu__mean": [1.0]},
        index=pd.date_range("2024-01-01", periods=1, freq="60s", tz="UTC"),
    )
    disabled = FeatureSettings(canonical_enabled=False)
    assert apply_canonical_features(raw, disabled) is raw

    enabled = FeatureSettings(canonical_enabled=True)
    out = apply_canonical_features(raw, enabled)
    assert out.shape[1] == 35
    assert out["canonical__cpu__mean"].iloc[0] == pytest.approx(1.0)


def test_align_features_to_model():
    trained = ["canonical__cpu__mean", "canonical__memory__mean"]
    live = pd.DataFrame(
        {
            "canonical__cpu__mean": [0.5],
            "canonical__network__mean": [9.9],
        },
        index=pd.date_range("2024-01-01", periods=1, freq="60s", tz="UTC"),
    )
    aligned = align_features_to_model(live, trained)
    assert list(aligned.columns) == trained
    assert aligned["canonical__memory__mean"].iloc[0] == pytest.approx(0.0)


def test_detector_reindex_avoids_mismatch(mapper: CanonicalFeatureMapper):
    train_df = mapper.transform(
        pd.DataFrame(
            {
                "export__node_cpu__mean": [0.1, 0.2, 0.3, 0.4, 0.5] * 4,
                "export__node_memory__mean": [1.0, 1.1, 1.2, 1.3, 1.4] * 4,
            },
            index=pd.date_range("2024-01-01", periods=20, freq="60s", tz="UTC"),
        )
    )
    detector = AnomalyDetector(contamination=0.1)
    detector.fit(train_df)

    live_raw = pd.DataFrame(
        {
            "export__node_cpu__mean": [0.9] * 20,
            "export__node_network_receive_bytes__mean": [5.0] * 20,
        },
        index=pd.date_range("2024-02-01", periods=20, freq="60s", tz="UTC"),
    )
    live_canon = mapper.transform(live_raw)
    assert live_canon.shape[1] == train_df.shape[1]

    clusters = detector.detect(live_canon)
    assert isinstance(clusters, list)
