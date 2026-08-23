"""
Unit tests for settings resiliency, malformed YAML recovery, and scenario ranker weight normalization.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from chaosgen.config.settings import load_settings, save_settings, ChaosGenSettings, RankingSettings
from chaosgen.advisor.scenario_ranker import ScenarioRanker, FaultType
from chaosgen.schemas.faults import ChaosExperiment, TargetSpec, TargetType, NetworkFaultSpec


def _make_experiment(name: str) -> ChaosExperiment:
    return ChaosExperiment(
        name=name,
        target=TargetSpec(type=TargetType.SERVICE, name="test-svc"),
        faults=[NetworkFaultSpec(fault_type=FaultType.NETWORK_LATENCY, duration="30s", latency="100ms")],
    )


def test_settings_resiliency_malformed_yaml():
    """Verify that load_settings falls back to defaults when reading malformed YAML."""
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_file = Path(tmpdir) / "settings.yaml"
        # Write corrupted YAML content
        settings_file.write_text("invalid_yaml: [malformed: yes: {", encoding="utf-8")

        settings = load_settings(path=str(settings_file))
        assert isinstance(settings, ChaosGenSettings)
        # Should have fallback default values
        assert settings.features.zscore_threshold == 3.0
        assert settings.anomaly.clustering_mode == "auto"


def test_settings_resiliency_invalid_pydantic_values():
    """Verify that load_settings falls back to defaults when reading invalid schema fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_file = Path(tmpdir) / "settings.yaml"
        # Write invalid zscore (Pydantic validation limit is ge=0.5)
        settings_file.write_text("features:\n  zscore_threshold: -5.0\n", encoding="utf-8")

        settings = load_settings(path=str(settings_file))
        assert isinstance(settings, ChaosGenSettings)
        # Should drop back to defaults on validation failure
        assert settings.features.zscore_threshold == 3.0


def test_ranker_weight_normalization_even_weights():
    """Verify that equal raw weights result in equal normalized weights under the hood."""
    ranking_settings = RankingSettings(
        weight_confidence=1.0,
        weight_historical=1.0,
        weight_coverage=1.0,
        weight_safety=1.0,
    )
    ranker = ScenarioRanker(settings=ranking_settings)
    assert ranker._w_confidence == 0.25
    assert ranker._w_historical == 0.25
    assert ranker._w_coverage == 0.25
    assert ranker._w_safety == 0.25


def test_ranker_weight_normalization_uneven_weights():
    """Verify that arbitrary raw weights normalize correctly to sum to 1.0."""
    ranking_settings = RankingSettings(
        weight_confidence=3.0,
        weight_historical=2.0,
        weight_coverage=1.0,
        weight_safety=0.0,
    )
    ranker = ScenarioRanker(settings=ranking_settings)
    assert ranker._w_confidence == pytest.approx(0.50)
    assert ranker._w_historical == pytest.approx(1/3)
    assert ranker._w_coverage == pytest.approx(1/6)
    assert ranker._w_safety == 0.0
    assert ranker._w_confidence + ranker._w_historical + ranker._w_coverage + ranker._w_safety == pytest.approx(1.0)


def test_ranker_weight_normalization_all_zero_fallback():
    """All-zero ranking weights are rejected by P8 RankingSettings validation."""
    with pytest.raises(ValueError, match="positive"):
        RankingSettings(
            weight_confidence=0.0,
            weight_historical=0.0,
            weight_coverage=0.0,
            weight_safety=0.0,
        )
