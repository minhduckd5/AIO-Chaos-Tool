"""Execution context honesty + AI steady-state default."""

from pathlib import Path

from chaosgen.advisor.scenario_generator import ScenarioGenerator
from chaosgen.config.settings import ChaosGenSettings
from chaosgen.gui.execution_context import format_execution_context
from chaosgen.schemas.faults import FaultType
from chaosgen.schemas.scenarios import FaultHypothesis


def test_ai_generated_experiment_has_no_http_health_steady_state():
    gen = ScenarioGenerator()
    hyp = FaultHypothesis(
        fault_type=FaultType.PROCESS_KILL,
        target_hint="checkoutservice",
        rationale="test",
        confidence=0.9,
        source_cluster_id=0,
    )
    exps = gen.generate([hyp])
    assert len(exps) == 1
    assert exps[0].steady_state_check is None


def test_execution_context_undetermined_without_kube_facts(tmp_path, monkeypatch):
    settings = ChaosGenSettings()
    settings.inject.kubeconfig = None
    settings.inject.context = None
    if settings.connect.kubernetes is not None:
        settings.connect.kubernetes.kubeconfig = None
        settings.connect.kubernetes.context = None
    # Avoid picking a real default kubeconfig via accidental path
    text = format_execution_context(settings)
    assert "not determined" in text.lower()


def test_execution_context_shows_existing_kubeconfig(tmp_path):
    kube = tmp_path / "config"
    kube.write_text("apiVersion: v1\n", encoding="utf-8")
    settings = ChaosGenSettings()
    settings.inject.kubeconfig = str(kube)
    settings.inject.context = "lab"
    settings.inject.default_namespace = "default"
    text = format_execution_context(settings)
    assert "not determined" not in text.lower()
    assert "lab" in text
    assert "default" in text
    assert "Steady-state" in text
