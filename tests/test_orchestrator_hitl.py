"""Tests for the HITL approval gate in ChaosOrchestrator."""
import json

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

    def test_second_approve_after_idle_requeues_pending(self):
        """Regression: can't trigger approve_experiment from state idle."""
        orch = ChaosOrchestrator()
        report = _make_report(2)
        orch.run_ai_experiment(report)
        assert orch.state == "pending_approval"
        # Simulate end-of-run: FSM idle, queue still populated (HITL multi-approve).
        orch.machine.set_state("idle")
        assert orch.state == "idle"
        assert len(orch.pending_experiments) == 2
        result = orch.approve_and_run(1)
        assert result.get("ran") is True
        assert orch.current_experiment.name == "ai-exp-1"

    def test_requeue_emits_queued_audit(self, tmp_path):
        from chaosgen.storage.audit import AuditStore

        orch = ChaosOrchestrator()
        audit_path = tmp_path / "audit.jsonl"
        orch.audit_store = AuditStore(path=audit_path)
        orch._audit_actor = "tester"
        orch.run_ai_experiment(_make_report(2))
        orch.machine.set_state("idle")
        orch._requeue_pending_after_run()
        rows = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        requeue = [
            r
            for r in rows
            if r.get("event_type") == "queued"
            and "requeue after prior run" in str(r.get("notes") or "")
        ]
        assert len(requeue) == 1
        assert orch.state == "pending_approval"


class TestAntiReapproveGuard:
    """Phase 1: same queue must not inject the same experiment name twice."""

    def test_sequential_double_approve_same_index_blocked(self):
        orch = ChaosOrchestrator()
        orch.run_ai_experiment(_make_report(1))
        assert orch.state == "pending_approval"

        # Fast path past steady-state into inject without cluster I/O.
        orch.validator.validate = lambda _c: True
        orch._start_dead_mans_switch = lambda: None

        def _fast_inject():
            orch.last_outcome = "PASS"
            orch.injection_complete()

        def _fast_verify():
            orch.verification_complete()

        orch._execute_injection = _fast_inject
        orch._run_verification = _fast_verify

        first = orch.approve_and_run(0)
        assert first.get("ran") is True
        assert "ai-exp-0" in orch._consumed_approvals

        second = orch.approve_and_run(0)
        assert second.get("ran") is False
        assert "already approved this queue" in str(second.get("reason") or "")

    def test_blocked_namespace_discards_token_for_rereview(self):
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
        first = orch.approve_and_run(0)
        assert first.get("ran") is True
        assert orch.last_outcome == "FAIL"
        assert "kube-system-hit" not in orch._consumed_approvals

        # Pre-inject abort released the token — second approve may enter FSM again.
        second = orch.approve_and_run(0)
        assert second.get("ran") is True
        assert "already approved this queue" not in str(second.get("reason") or "")

    def test_unexpected_exception_discards_consumed_token(self):
        orch = ChaosOrchestrator()
        orch.run_ai_experiment(_make_report(1))
        name = orch.pending_experiments[0].name

        def _boom_validate(_exp):
            raise RuntimeError("unexpected pre-inject failure")

        orch.blast_radius_controller.validate_experiment = _boom_validate
        with pytest.raises(RuntimeError, match="unexpected pre-inject failure"):
            orch.approve_and_run(0)
        assert name not in orch._consumed_approvals

    def test_reject_all_clears_consumed_approvals(self):
        orch = ChaosOrchestrator()
        orch.run_ai_experiment(_make_report(1))
        orch._consumed_approvals.add("ai-exp-0")
        orch.reject_all()
        assert orch._consumed_approvals == set()

    def test_clear_pending_clears_consumed_approvals(self):
        orch = ChaosOrchestrator()
        orch.run_ai_experiment(_make_report(1))
        orch._consumed_approvals.add("ai-exp-0")
        orch._clear_pending()
        assert orch._consumed_approvals == set()

    def test_run_ai_experiment_clears_consumed_without_clear_pending(self):
        """Plan #5 second clause: new queue load clears tokens by itself."""
        orch = ChaosOrchestrator()
        orch.run_ai_experiment(_make_report(1))
        orch._consumed_approvals.add("stale-token")
        # FSM must be idle to accept submit_for_approval; do NOT call _clear_pending.
        orch.machine.set_state("idle")
        assert "stale-token" in orch._consumed_approvals
        orch.run_ai_experiment(_make_report(1))
        assert orch._consumed_approvals == set()

    def test_run_ai_experiment_replaces_queue_while_pending_approval(self):
        """Calling run_ai_experiment while already pending_approval must not raise MachineError."""
        orch = ChaosOrchestrator()
        orch.run_ai_experiment(_make_report(1))
        assert orch.state == "pending_approval"
        assert len(orch.pending_experiments) == 1
        assert orch.pending_experiments[0].name == "ai-exp-0"

        # Re-submitting a new report (e.g. user selected another catalog item) succeeds
        report2 = _make_report(2)
        orch.run_ai_experiment(report2)
        assert orch.state == "pending_approval"
        assert len(orch.pending_experiments) == 2
        assert orch.pending_experiments[0].name == "ai-exp-0"
        assert orch.pending_experiments[1].name == "ai-exp-1"

    def test_reject_pending_at_single_item(self):
        orch = ChaosOrchestrator()
        orch.run_ai_experiment(_make_report(2))
        assert len(orch.pending_experiments) == 2

        # Invalid index
        assert orch.reject_pending_at(-1) is False
        assert orch.reject_pending_at(5) is False
        assert len(orch.pending_experiments) == 2

        # Reject index 0: removes first, 1 remaining, stays in pending_approval
        assert orch.reject_pending_at(0) is True
        assert len(orch.pending_experiments) == 1
        assert orch.pending_experiments[0].name == "ai-exp-1"
        assert orch.state == "pending_approval"

        # Reject remaining item: queue empty -> transitions to idle
        assert orch.reject_pending_at(0) is True
        assert len(orch.pending_experiments) == 0
        assert orch.state == "idle"


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
        # Queue still holds the blocked scenario — return to pending_approval
        # so a later Approve/Reject remains possible (multi-HITL).
        assert orch.state == "pending_approval"
        assert len(orch.pending_experiments) == 1
        # MODIFIED: pre-inject FAIL must release anti-reapprove token
        assert "kube-system-hit" not in orch._consumed_approvals

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
