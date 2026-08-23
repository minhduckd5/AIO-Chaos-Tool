"""P8 — analysis & ranking tuning settings / wiring tests."""

from __future__ import annotations

import pytest

from chaosgen.config.settings import (
    AdvisorSettings,
    ChaosGenSettings,
    FeatureSettings,
    IngestSettings,
    RankingSettings,
    SafetySettings,
)
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.safety.governance import SafetyPolicy
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    ProcessFaultSpec,
    TargetSpec,
    TargetType,
)


def test_feature_settings_affect_row_count():
    from tests.test_ml_pipeline import _make_dataset

    dataset = _make_dataset(n_samples=400, anomaly_fraction=0.1)
    coarse = FeatureEngineer(
        settings=FeatureSettings(rolling_window_seconds=300, resample_step_seconds=120)
    )
    fine = FeatureEngineer(
        settings=FeatureSettings(rolling_window_seconds=300, resample_step_seconds=30)
    )
    assert fine.step == 30
    assert coarse.step == 120
    n_coarse = len(coarse.transform(dataset))
    n_fine = len(fine.transform(dataset))
    assert n_coarse > 0 and n_fine > 0
    assert n_fine > n_coarse


def test_ranker_weights_normalize():
    settings = RankingSettings(
        weight_confidence=2.0,
        weight_historical=2.0,
        weight_coverage=2.0,
        weight_safety=2.0,
    )
    total = (
        settings.weight_confidence
        + settings.weight_historical
        + settings.weight_coverage
        + settings.weight_safety
    )
    assert abs(total - 1.0) < 0.01


def test_ranker_weights_zero_rejected():
    with pytest.raises(ValueError, match="positive"):
        RankingSettings(
            weight_confidence=0,
            weight_historical=0,
            weight_coverage=0,
            weight_safety=0,
        )


def test_feature_aliases_from_plan_yaml_keys():
    settings = FeatureSettings.model_validate(
        {"window_size_seconds": 120, "step_seconds": 30, "zscore_threshold": 2.5}
    )
    assert settings.rolling_window_seconds == 120
    assert settings.resample_step_seconds == 30


def test_ingest_custom_promql_rejects_empty():
    with pytest.raises(ValueError):
        IngestSettings(custom_promql={"bad": "  "})


def test_advisor_defaults():
    a = AdvisorSettings()
    assert a.confidence_threshold == 0.6
    assert a.top_n_scenarios == 5
    assert a.describer_max_retries == 2


def test_safety_policy_from_settings():
    policy = SafetyPolicy.from_settings(
        SafetySettings(
            max_affected_nodes=3,
            blocked_namespaces=["kube-system", "foo"],
            blocked_services=["db"],
        )
    )
    assert policy.max_affected_nodes == 3
    assert "foo" in policy.blocked_namespaces
    exp = ChaosExperiment(
        name="x",
        target=TargetSpec(type=TargetType.SERVICE, name="db", namespace="default"),
        faults=[ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL)],
    )
    from chaosgen.safety.governance import BlastRadiusController

    with pytest.raises(ValueError, match="protected"):
        BlastRadiusController(policy).validate_experiment(exp)


def test_settings_roundtrip_yaml(tmp_path):
    from chaosgen.config.settings import load_settings, save_settings

    path = tmp_path / "settings.yaml"
    original = ChaosGenSettings(
        advisor=AdvisorSettings(confidence_threshold=0.75, top_n_scenarios=7),
        features=FeatureSettings(resample_step_seconds=30),
        ingest=IngestSettings(log_query='{app="payments"}'),
        ranking=RankingSettings(recency_days=14),
    )
    save_settings(original, path)
    loaded = load_settings(str(path))
    assert loaded.advisor.confidence_threshold == 0.75
    assert loaded.advisor.top_n_scenarios == 7
    assert loaded.features.resample_step_seconds == 30
    assert loaded.ingest.log_query == '{app="payments"}'
    assert loaded.ranking.recency_days == 14


def test_confidence_threshold_drops_hypotheses():
    from chaosgen.advisor.scenario_generator import ScenarioGenerator
    from chaosgen.schemas.scenarios import FaultHypothesis

    gen = ScenarioGenerator(confidence_threshold=0.9)
    hyps = [
        FaultHypothesis(
            fault_type=FaultType.PROCESS_KILL,
            target_hint="api",
            rationale="kill test",
            confidence=0.5,
            source_cluster_id=0,
        ),
        FaultHypothesis(
            fault_type=FaultType.NETWORK_LATENCY,
            target_hint="api",
            rationale="latency test",
            confidence=0.95,
            source_cluster_id=1,
        ),
    ]
    experiments = gen.generate(hyps)
    assert len(experiments) == 1
