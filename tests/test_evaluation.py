"""Tests for the Evaluation Framework: KPITracker and ABComparator."""
import json
import pytest

from chaosgen.evaluation.kpi_tracker import KPITracker
from chaosgen.evaluation.ab_comparator import ABComparator
from chaosgen.schemas.faults import (
    ChaosExperiment, TargetSpec, TargetType, FaultType,
    NetworkFaultSpec, ProcessFaultSpec,
)


class TestKPITracker:
    def test_actionability_rate(self):
        tracker = KPITracker()
        tracker.record_experiment_result("exp-1", source="ai", accepted=True, modifications=0)
        tracker.record_experiment_result("exp-2", source="ai", accepted=True, modifications=2)
        tracker.record_experiment_result("exp-3", source="ai", accepted=False, modifications=0)

        rate = tracker.compute_actionability_rate("ai")
        assert rate == pytest.approx(33.33, abs=0.1)

    def test_discovery_rate(self):
        tracker = KPITracker()
        tracker.record_experiment_result("exp-1", source="ai", accepted=True, new_failures_found=3)
        tracker.record_experiment_result("exp-2", source="ai", accepted=True, new_failures_found=1)

        rate = tracker.compute_discovery_rate("ai")
        assert rate == 2.0

    def test_time_to_design(self):
        tracker = KPITracker()
        tracker.record_experiment_result("exp-1", source="ai", accepted=True, design_time_minutes=5.0)
        tracker.record_experiment_result("exp-2", source="ai", accepted=True, design_time_minutes=15.0)

        avg_time = tracker.compute_time_to_design("ai")
        assert avg_time == 10.0

    def test_empty_tracker(self):
        tracker = KPITracker()
        assert tracker.compute_actionability_rate("ai") == 0.0
        assert tracker.compute_discovery_rate("ai") == 0.0
        assert tracker.compute_time_to_design("ai") == 0.0

    def test_compute_all_kpis(self):
        tracker = KPITracker()
        tracker.record_experiment_result("a1", source="ai", accepted=True, modifications=0)
        tracker.record_experiment_result("h1", source="human", accepted=True, modifications=0)
        kpis = tracker.compute_all_kpis()
        assert "ai" in kpis
        assert "human" in kpis
        assert kpis["ai"]["total_experiments"] == 1

    def test_export_json(self, tmp_path):
        tracker = KPITracker()
        tracker.record_experiment_result("exp-1", source="ai", accepted=True)
        path = str(tmp_path / "report.json")
        tracker.export_report(path, format="json")
        data = json.loads((tmp_path / "report.json").read_text())
        assert "kpis" in data
        assert "records" in data

    def test_export_csv(self, tmp_path):
        tracker = KPITracker()
        tracker.record_experiment_result("exp-1", source="ai", accepted=True)
        path = str(tmp_path / "report.csv")
        tracker.export_report(path, format="csv")
        content = (tmp_path / "report.csv").read_text()
        assert "experiment_name" in content


def _make_experiment(name, fault_type=FaultType.NETWORK_LATENCY):
    if fault_type == FaultType.NETWORK_LATENCY:
        fault = NetworkFaultSpec(fault_type=fault_type, duration="30s", latency="100ms")
    else:
        fault = ProcessFaultSpec(fault_type=fault_type, duration="10s")
    return ChaosExperiment(
        name=name,
        target=TargetSpec(type=TargetType.SERVICE, name="svc"),
        faults=[fault],
    )


class TestABComparator:
    def test_comparison_report(self):
        tracker = KPITracker()
        tracker.record_experiment_result("h1", source="human", accepted=True, modifications=0,
                                         new_failures_found=1, design_time_minutes=60)
        tracker.record_experiment_result("a1", source="ai", accepted=True, modifications=0,
                                         new_failures_found=3, design_time_minutes=5)

        comparator = ABComparator(kpi_tracker=tracker)
        comparator.register_human_scenarios([_make_experiment("h1")])
        comparator.register_ai_scenarios([_make_experiment("a1")])

        report = comparator.run_comparison()
        assert report.discovery_delta > 0
        assert report.time_savings_percent > 0
        assert "AI actionability" in report.actionability_comparison

    def test_empty_comparison(self):
        comparator = ABComparator()
        report = comparator.run_comparison()
        assert report.discovery_delta == 0.0
        assert report.human_sci_stats["count"] == 0

    def test_export_report(self, tmp_path):
        tracker = KPITracker()
        tracker.record_experiment_result("a1", source="ai", accepted=True)
        comparator = ABComparator(kpi_tracker=tracker)
        comparator.register_ai_scenarios([_make_experiment("a1")])
        path = str(tmp_path / "ab_report.json")
        comparator.export_report(path)
        data = json.loads((tmp_path / "ab_report.json").read_text())
        assert "ai_kpis" in data
        assert "ai_sci_stats" in data
