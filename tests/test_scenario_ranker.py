"""
Tests for ScenarioRanker — composite scoring and ranking.
"""

from __future__ import annotations

import pytest

from chaosgen.advisor.scenario_ranker import RankedScenario, ScenarioRanker
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    NetworkFaultSpec,
    TargetSpec,
    TargetType,
)


def _make_experiment(name: str, fault_type: FaultType = FaultType.NETWORK_LATENCY) -> ChaosExperiment:
    return ChaosExperiment(
        name=name,
        target=TargetSpec(type=TargetType.SERVICE, name="test-svc"),
        faults=[NetworkFaultSpec(fault_type=fault_type, duration="30s", latency="100ms")],
    )


class TestScenarioRanker:
    @pytest.fixture
    def ranker(self):
        return ScenarioRanker()  # no KPITracker or BlastRadiusController → defaults

    def test_rank_returns_ranked_scenarios(self, ranker):
        exps = [_make_experiment(f"exp-{i}") for i in range(3)]
        ranked = ranker.rank(exps)
        assert len(ranked) == 3
        for r in ranked:
            assert isinstance(r, RankedScenario)

    def test_top_n_limits_results(self, ranker):
        exps = [_make_experiment(f"exp-{i}") for i in range(10)]
        ranked = ranker.rank(exps, top_n=3)
        assert len(ranked) == 3

    def test_sorted_by_rank_score_descending(self, ranker):
        exps = [_make_experiment(f"exp-{i}") for i in range(5)]
        confidences = {f"exp-{i}": (i + 1) / 5.0 for i in range(5)}
        ranked = ranker.rank(exps, confidences=confidences)
        scores = [r.rank_score for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_high_confidence_boosts_score(self, ranker):
        exp_high = _make_experiment("high-conf")
        exp_low = _make_experiment("low-conf")
        ranked = ranker.rank(
            [exp_low, exp_high],
            confidences={"high-conf": 0.95, "low-conf": 0.1},
        )
        assert ranked[0].experiment.name == "high-conf"

    def test_coverage_gap_penalizes_repeated_fault_type(self, ranker):
        """An experiment whose fault type was recently tested should score lower on coverage."""
        from chaosgen.schemas.faults import FaultType
        exp_latency = _make_experiment("latency-exp", FaultType.NETWORK_LATENCY)

        # Inject a "recent fault type" artificially
        from chaosgen.advisor.scenario_ranker import _RECENCY_WINDOW_SECONDS
        coverage = ranker._compute_coverage_gap(exp_latency, recent_fault_types={FaultType.NETWORK_LATENCY})
        coverage_fresh = ranker._compute_coverage_gap(exp_latency, recent_fault_types=set())
        assert coverage < coverage_fresh

    def test_rank_score_between_zero_and_one(self, ranker):
        exps = [_make_experiment("test")]
        ranked = ranker.rank(exps)
        assert 0.0 <= ranked[0].rank_score <= 1.0

    def test_source_label_propagated(self, ranker):
        exp = _make_experiment("cat-exp")
        ranked = ranker.rank([exp], sources={"cat-exp": "catalog"})
        assert ranked[0].source == "catalog"

    def test_summary_returns_string(self, ranker):
        exp = _make_experiment("summary-test")
        ranked = ranker.rank([exp])
        summary = ranked[0].summary()
        assert isinstance(summary, str)
        assert "summary-test" in summary
