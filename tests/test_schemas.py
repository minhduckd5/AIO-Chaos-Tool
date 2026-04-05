"""Tests for telemetry and scenario schemas."""
import pytest
from datetime import datetime, timezone

from chaosgen.schemas.telemetry import (
    MetricSample, TimeSeries, LogEntry, LogStream,
    TelemetryDataset, TelemetrySnapshot,
)
from chaosgen.schemas.scenarios import (
    AnomalyCluster, AnomalySeverity, AnomalySummary,
    FaultHypothesis, ScenarioComplexityIndex, AdvisorReport,
)
from chaosgen.schemas.faults import FaultType


class TestTimeSeries:
    def test_values_property(self):
        ts = TimeSeries(
            metric_name="cpu",
            samples=[
                MetricSample(timestamp=1.0, value=0.5),
                MetricSample(timestamp=2.0, value=0.8),
            ],
        )
        assert ts.values == [0.5, 0.8]
        assert ts.timestamps == [1.0, 2.0]

    def test_empty_series(self):
        ts = TimeSeries(metric_name="empty")
        assert ts.values == []
        assert ts.timestamps == []


class TestTelemetryDataset:
    def test_duration_and_counts(self):
        ds = TelemetryDataset(
            metrics=[
                TimeSeries(
                    metric_name="m1",
                    samples=[MetricSample(timestamp=1.0, value=1.0)],
                )
            ],
            logs=[],
            collection_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            collection_end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert ds.duration_seconds == 86400.0
        assert ds.total_samples == 1
        assert ds.metric_names == ["m1"]


class TestAnomalySummary:
    def test_prompt_block_rendering(self):
        summary = AnomalySummary(
            service_name="order-service",
            top_features=[("cpu_usage", 4.2), ("error_rate", 3.1)],
            error_pattern="NullPointerException",
            severity=0.8,
            time_window="2024-03-10 14:00 - 14:15 UTC",
            source_cluster_id=0,
        )
        block = summary.to_prompt_block()
        assert "order-service" in block
        assert "cpu_usage" in block
        assert "NullPointerException" in block
        assert "0.80" in block

    def test_prompt_block_without_error_pattern(self):
        summary = AnomalySummary(
            service_name="api-gw",
            top_features=[("latency_p99", 5.0)],
            severity=0.5,
            time_window="test",
            source_cluster_id=1,
        )
        block = summary.to_prompt_block()
        assert "Error pattern" not in block


class TestScenarioComplexityIndex:
    def test_compute_basic(self):
        sci = ScenarioComplexityIndex(
            fault_cardinality=2,
            target_diversity=3,
            temporal_stages=1,
            blast_radius_percent=10.0,
            causal_chain_depth=2,
        )
        score = sci.compute()
        assert score > 0
        assert sci.weighted_score == score

    def test_minimal_complexity(self):
        sci = ScenarioComplexityIndex(
            fault_cardinality=1,
            target_diversity=1,
            temporal_stages=1,
            blast_radius_percent=0.0,
            causal_chain_depth=1,
        )
        score = sci.compute()
        assert 0 < score < 1


class TestFaultHypothesis:
    def test_valid_construction(self):
        hyp = FaultHypothesis(
            fault_type=FaultType.NETWORK_LATENCY,
            target_hint="api-gateway",
            rationale="High p99 latency correlated with network interface saturation",
            confidence=0.85,
            source_cluster_id=0,
            suggested_duration="60s",
            suggested_parameters={"latency": "200ms", "jitter": "20ms"},
        )
        assert hyp.confidence == 0.85
        assert hyp.fault_type == FaultType.NETWORK_LATENCY

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            FaultHypothesis(
                fault_type=FaultType.PROCESS_KILL,
                target_hint="x",
                rationale="test",
                confidence=1.5,
                source_cluster_id=0,
            )
