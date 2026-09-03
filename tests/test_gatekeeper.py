"""Tests for the IncidentGatekeeper (P1 — Gatekeeper Real Filter)."""
import pytest

from chaosgen.config.settings import GatekeeperSettings
from chaosgen.ml.gatekeeper import IncidentGatekeeper, InMemoryLookbackStateStore
from chaosgen.schemas.incidents import IncidentVerdict
from chaosgen.schemas.scenarios import (
    AdvisorReport,
    AnomalyCluster,
    AnomalySeverity,
    AnomalySummary,
)


def _cluster(
    cluster_id=0,
    severity=AnomalySeverity.LOW,
    sample_count=2,
    dominant_features=None,
    affected=None,
):
    return AnomalyCluster(
        cluster_id=cluster_id,
        severity=severity,
        affected_services=affected if affected is not None else ["svc-a"],
        dominant_features=dominant_features if dominant_features is not None
        else [("cpu_usage__svc-a__mean", 2.0)],
        sample_count=sample_count,
    )


def _summary(cluster_id=0, service="svc-a", error_pattern=None):
    return AnomalySummary(
        service_name=service,
        top_features=[],
        error_pattern=error_pattern,
        severity=0.5,
        time_window="2024-01-01 00:00 - 00:15 UTC",
        source_cluster_id=cluster_id,
    )


class TestVerdictMatrix:
    """4-cell frequency x severity matrix (window_hours=10)."""

    def test_noise_low_freq_low_severity(self):
        gk = IncidentGatekeeper()
        # freq = 2/10 = 0.2/h (<=0.5), severity LOW=0.25 (<=0.4)
        candidates, dropped = gk.filter([_cluster(sample_count=2, severity=AnomalySeverity.LOW)], 10.0)
        assert candidates == []
        assert dropped == 1

    def test_transient_low_freq_high_severity(self):
        gk = IncidentGatekeeper()
        # freq = 0.2/h (low), severity CRITICAL=1.0 (high) -> TRANSIENT
        candidates, dropped = gk.filter([_cluster(sample_count=2, severity=AnomalySeverity.CRITICAL)], 10.0)
        assert dropped == 0
        assert len(candidates) == 1
        assert candidates[0].verdict == IncidentVerdict.TRANSIENT

    def test_real_high_freq_high_severity(self):
        gk = IncidentGatekeeper()
        # freq = 30/10 = 3.0/h (>=2.0, not > 4.0), severity HIGH=0.75 -> REAL
        candidates, dropped = gk.filter([_cluster(sample_count=30, severity=AnomalySeverity.HIGH)], 10.0)
        assert dropped == 0
        assert candidates[0].verdict == IncidentVerdict.REAL

    def test_chronic_very_high_freq_high_severity(self):
        gk = IncidentGatekeeper()
        # freq = 50/10 = 5.0/h (> 4.0 = freq_high*2), severity CRITICAL -> CHRONIC
        candidates, dropped = gk.filter([_cluster(sample_count=50, severity=AnomalySeverity.CRITICAL)], 10.0)
        assert candidates[0].verdict == IncidentVerdict.CHRONIC


class TestStrictLogBoost:
    def _high_freq_low_sev_cluster(self):
        # freq = 30/10 = 3.0/h (high), severity LOW=0.25 (low) -> base TRANSIENT
        return _cluster(
            sample_count=30,
            severity=AnomalySeverity.LOW,
            dominant_features=[("http_error_rate__svc-a__mean", 3.5)],
        )

    def test_boost_transient_to_real_with_metric_and_severe_log(self):
        gk = IncidentGatekeeper()
        summaries = [_summary(error_pattern="error: elevated http_error_rate (z=3.50)")]
        candidates, _ = gk.filter([self._high_freq_low_sev_cluster()], 10.0, summaries=summaries)
        assert candidates[0].verdict == IncidentVerdict.REAL
        assert candidates[0].log_correlated is True

    def test_no_boost_for_warning_only_logs(self):
        gk = IncidentGatekeeper(GatekeeperSettings(service_error_boost=False))
        summaries = [_summary(error_pattern="warning: deprecation notice")]
        candidates, _ = gk.filter([self._high_freq_low_sev_cluster()], 10.0, summaries=summaries)
        assert candidates[0].verdict == IncidentVerdict.TRANSIENT
        assert candidates[0].log_correlated is False

    def test_no_boost_when_metric_signal_missing(self):
        gk = IncidentGatekeeper(GatekeeperSettings(service_error_boost=False))
        # severe log present, but no error_rate/error_count in dominant features
        cluster = _cluster(
            sample_count=30,
            severity=AnomalySeverity.LOW,
            dominant_features=[("cpu_usage__svc-a__mean", 3.5)],
        )
        summaries = [_summary(error_pattern="error: elevated something")]
        candidates, _ = gk.filter([cluster], 10.0, summaries=summaries)
        assert candidates[0].verdict == IncidentVerdict.TRANSIENT
        assert candidates[0].log_correlated is False

    def test_boost_disabled_via_settings(self):
        gk = IncidentGatekeeper(
            GatekeeperSettings(strict_log_boost=False, service_error_boost=False)
        )
        summaries = [_summary(error_pattern="error: elevated http_error_rate")]
        candidates, _ = gk.filter([self._high_freq_low_sev_cluster()], 10.0, summaries=summaries)
        assert candidates[0].verdict == IncidentVerdict.TRANSIENT


class TestEdgeCases:
    def test_empty_cluster_list(self):
        gk = IncidentGatekeeper()
        candidates, dropped = gk.filter([], 10.0)
        assert candidates == []
        assert dropped == 0

    def test_invalid_window_hours_raises(self):
        gk = IncidentGatekeeper()
        with pytest.raises(ValueError):
            gk.filter([_cluster()], 0.0)

    def test_filtered_noise_count_in_advisor_report(self):
        gk = IncidentGatekeeper()
        clusters = [
            _cluster(cluster_id=0, sample_count=2, severity=AnomalySeverity.LOW),       # NOISE
            _cluster(cluster_id=1, sample_count=30, severity=AnomalySeverity.HIGH),     # REAL
        ]
        candidates, dropped = gk.filter(clusters, 10.0)
        report = AdvisorReport(
            anomalies_found=len(clusters),
            incident_candidates=candidates,
            filtered_noise_count=dropped,
        )
        assert report.filtered_noise_count == 1
        assert len(report.incident_candidates) == 1


class TestCandidateContext:
    def test_candidate_carries_service_target_and_metadata(self):
        gk = IncidentGatekeeper()
        clusters = [_cluster(sample_count=30, severity=AnomalySeverity.HIGH, affected=["payments"])]
        candidates, _ = gk.filter(clusters, 10.0)
        candidate = candidates[0]
        assert candidate.service_target == "payments"
        assert "sample_count" in candidate.metadata
        assert candidate.metadata["window_hours"] == 10.0

    def test_service_target_prefers_summary_service_name(self):
        gk = IncidentGatekeeper()
        clusters = [_cluster(sample_count=30, severity=AnomalySeverity.HIGH, affected=["raw-svc"])]
        summaries = [_summary(service="resolved-svc")]
        candidates, _ = gk.filter(clusters, 10.0, summaries=summaries)
        assert candidates[0].service_target == "resolved-svc"


class TestLookbackState:
    def test_in_memory_state_accumulates_frequency_across_batches(self):
        gk = IncidentGatekeeper()
        store = InMemoryLookbackStateStore()

        # Batch 1: 30 samples / 10h = 3.0/h
        b1, _ = gk.filter(
            [_cluster(sample_count=30, severity=AnomalySeverity.HIGH, affected=["svc-a"])],
            10.0, state_store=store,
        )
        assert b1[0].frequency == pytest.approx(3.0)

        # Batch 2 alone would be 10/10 = 1.0/h, but cumulative = 40/20 = 2.0/h
        b2, _ = gk.filter(
            [_cluster(sample_count=10, severity=AnomalySeverity.HIGH, affected=["svc-a"])],
            10.0, state_store=store,
        )
        assert b2[0].frequency == pytest.approx(2.0)

    def test_without_state_each_batch_is_independent(self):
        gk = IncidentGatekeeper()
        candidates, _ = gk.filter(
            [_cluster(sample_count=10, severity=AnomalySeverity.HIGH, affected=["svc-a"])],
            10.0,
        )
        assert candidates[0].frequency == pytest.approx(1.0)
