"""Tests for the HITL approval gate in ChaosOrchestrator."""
import pytest

from chaosgen.orchestrator import ChaosOrchestrator
from chaosgen.schemas.faults import (
    ChaosExperiment, TargetSpec, TargetType, FaultType, ProcessFaultSpec,
)
from chaosgen.schemas.scenarios import AdvisorReport


def _make_experiment(name="test-exp"):
    return ChaosExperiment(
        name=name,
        target=TargetSpec(type=TargetType.SERVICE, name="test-svc"),
        faults=[ProcessFaultSpec(
            fault_type=FaultType.PROCESS_KILL, duration="10s",
        )],
    )


def _make_report(n_experiments=2):
    experiments = [_make_experiment(f"ai-exp-{i}") for i in range(n_experiments)]
    return AdvisorReport(
        anomalies_found=n_experiments,
        generated_experiments=experiments,
    )


class TestHITLApprovalGate:
    def test_initial_state_is_idle(self):
        orch = ChaosOrchestrator()
        assert orch.state == "idle"

    def test_pending_approval_states(self):
        orch = ChaosOrchestrator()
        assert "pending_approval" in ChaosOrchestrator.states

    def test_run_ai_experiment_enters_pending(self):
        orch = ChaosOrchestrator()
        report = _make_report(2)
        orch.run_ai_experiment(report)
        assert orch.state == "pending_approval"
        assert len(orch.pending_experiments) == 2

    def test_reject_all_returns_to_idle(self):
        orch = ChaosOrchestrator()
        report = _make_report(2)
        orch.run_ai_experiment(report)
        assert orch.state == "pending_approval"
        orch.reject_all()
        assert orch.state == "idle"
        assert len(orch.pending_experiments) == 0

    def test_get_pending_experiments(self):
        orch = ChaosOrchestrator()
        report = _make_report(3)
        orch.run_ai_experiment(report)
        pending = orch.get_pending_experiments()
        assert len(pending) == 3
        assert pending[0].name == "ai-exp-0"

    def test_empty_report_stays_idle(self):
        orch = ChaosOrchestrator()
        report = AdvisorReport(anomalies_found=0)
        orch.run_ai_experiment(report)
        assert orch.state == "idle"

    def test_approve_sets_current_experiment(self):
        orch = ChaosOrchestrator()
        report = _make_report(2)
        orch.run_ai_experiment(report)
        assert orch.state == "pending_approval"
        orch.approve_and_run(1)
        assert orch.current_experiment.name == "ai-exp-1"


class TestGatedInjectSafety:
    """G2/G3: inject-time blast radius + dead man's switch arming."""

    def test_blocked_namespace_rejected_on_approve_path(self):
        orch = ChaosOrchestrator()
        bad = ChaosExperiment(
            name="kube-system-hit",
            target=TargetSpec(
                type=TargetType.SERVICE, name="coredns", namespace="kube-system"
            ),
            faults=[
                ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="10s")
            ],
        )
        orch.run_ai_experiment(
            AdvisorReport(anomalies_found=1, generated_experiments=[bad])
        )
        assert orch.state == "pending_approval"
        orch.approve_and_run(0)
        assert orch.last_outcome == "FAIL"
        assert orch.state == "idle"

    def test_execute_injection_revalidates_blast_radius(self):
        orch = ChaosOrchestrator()
        orch.current_experiment = ChaosExperiment(
            name="tampered",
            target=TargetSpec(
                type=TargetType.SERVICE, name="coredns", namespace="kube-system"
            ),
            faults=[
                ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="10s")
            ],
        )
        orch._execute_injection()
        assert orch.last_outcome == "FAIL"

    def test_dead_mans_switch_starts_when_check_present(self):
        orch = ChaosOrchestrator()
        orch.current_experiment = _make_experiment()
        orch.current_experiment.steady_state_check = {
            "prometheus": {"url": "http://127.0.0.1:9090", "query": "up"}
        }
        orch.validator.validate = lambda _c: True
        orch._start_dead_mans_switch()
        assert orch.dead_mans_switch is not None
        orch._cleanup_safety()
        assert orch.dead_mans_switch is None
