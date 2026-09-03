"""Tests for per-service attribution helpers (Phase 2 RCA patch)."""

from __future__ import annotations

from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.canonical_features import extract_service_from_column, is_concrete_service
from chaosgen.ml.gatekeeper import IncidentGatekeeper
from chaosgen.schemas.incidents import IncidentVerdict
from chaosgen.schemas.scenarios import AnomalyCluster, AnomalySeverity, AnomalySummary


def test_extract_service_from_custom_column():
    col = "custom__error_rate__boutique:http_errors:rate5m__frontend__mean"
    assert extract_service_from_column(col) == "frontend"


def test_extract_service_from_per_service_canonical():
    col = "canonical__errors__checkoutservice__p95"
    assert extract_service_from_column(col) == "checkoutservice"


def test_extract_service_skips_signal_buckets():
    assert extract_service_from_column("canonical__latency__p95") is None
    assert extract_service_from_column("custom__request_rate__boutique:http_requests:rate5m__mean") is None


def test_anomaly_detector_service_extraction():
    dominant = [
        ("custom__error_rate__boutique:http_errors:rate5m__frontend__mean", 1.2),
        ("canonical__latency__p95", 0.5),
    ]
    services = AnomalyDetector._extract_service_names(dominant)
    assert services == ["frontend"]


def test_gatekeeper_service_error_boost():
    cluster = AnomalyCluster(
        cluster_id=0,
        severity=AnomalySeverity.LOW,
        affected_services=["frontend"],
        dominant_features=[
            ("custom__error_rate__boutique:http_errors:rate5m__frontend__mean", 2.0),
        ],
        sample_count=16,
    )
    summary = AnomalySummary(
        service_name="frontend",
        top_features=[],
        severity=0.25,
        time_window="2026-08-29 04:01 - 04:46 UTC",
        source_cluster_id=0,
    )
    gk = IncidentGatekeeper()
    candidates, dropped = gk.filter([cluster], 1.0, summaries=[summary])
    assert dropped == 0
    assert len(candidates) == 1
    assert candidates[0].verdict == IncidentVerdict.REAL
    assert candidates[0].service_target == "frontend"


def test_is_concrete_service():
    assert is_concrete_service("frontend")
    assert not is_concrete_service("latency")
    assert not is_concrete_service("unknown")
