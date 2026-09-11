"""Audit hook sequences A1/A2/A3/A4/A5/A7 — no live cluster required."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaosgen.orchestrator import ChaosOrchestrator
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    ProcessFaultSpec,
    TargetSpec,
    TargetType,
)
from chaosgen.schemas.scenarios import AdvisorReport
from chaosgen.storage.audit import AuditStore

ACTOR = "sre-anh"


def _experiment(name: str = "checkout-latency", namespace: str = "lab") -> ChaosExperiment:
    return ChaosExperiment(
        name=name,
        target=TargetSpec(type=TargetType.SERVICE, name="checkout", namespace=namespace),
        faults=[ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="10s")],
    )


@pytest.fixture
def orch(tmp_path: Path) -> ChaosOrchestrator:
    """Orchestrator wired to a temp audit log with inject stubbed out."""
    orchestrator = ChaosOrchestrator()
    store = AuditStore(tmp_path / "audit_events.jsonl")
    orchestrator.set_audit_context(actor=ACTOR, audit_store=store)

    # Keep the FSM local: no telemetry, no translate, no cluster.
    orchestrator.validator.validate = lambda _check: True
    orchestrator.translator.translate = lambda _exp: []
    orchestrator._start_dead_mans_switch = lambda: None
    orchestrator.inject_gc = lambda: {"success": True}
    if orchestrator._cg_settings is not None:
        orchestrator._cg_settings.inject.dry_run = True
        orchestrator._cg_settings.inject.context = "kind-lab"
    return orchestrator


def _sequence(orchestrator: ChaosOrchestrator):
    return [
        (event.event_type, event.path_used, event.outcome)
        for event in orchestrator.audit_store.read_events()
    ]


class TestA1AiHitl:
    def test_queue_approve_inject_sequence(self, orch: ChaosOrchestrator):
        orch.run_ai_experiment(
            AdvisorReport(anomalies_found=1, generated_experiments=[_experiment()])
        )
        orch.approve_and_run(0)

        assert _sequence(orch) == [
            ("queued", "ai_hitl", None),
            ("approved", "ai_hitl", None),
            ("inject_started", "ai_hitl", None),
            ("inject_finished", "ai_hitl", "dry_run"),
        ]

    def test_inject_events_share_run_id_with_approval(self, orch: ChaosOrchestrator):
        orch.run_ai_experiment(
            AdvisorReport(anomalies_found=1, generated_experiments=[_experiment()])
        )
        orch.approve_and_run(0)

        events = [
            e for e in orch.audit_store.read_events() if e.event_type != "queued"
        ]
        run_ids = {e.run_id for e in events}
        assert len(run_ids) == 1 and None not in run_ids
        assert {e.experiment_name for e in events} == {"checkout-latency"}
        assert {e.actor for e in events} == {ACTOR}

    def test_reject_records_decision(self, orch: ChaosOrchestrator):
        orch.run_ai_experiment(
            AdvisorReport(anomalies_found=1, generated_experiments=[_experiment()])
        )
        orch.reject_all()

        assert [e.event_type for e in orch.audit_store.read_events()] == [
            "queued",
            "rejected",
        ]


class TestA2OperatorDirect:
    def test_direct_run_emits_hatch_then_inject_pair(self, orch: ChaosOrchestrator):
        orch.run_experiment(_experiment())

        assert _sequence(orch) == [
            ("hatch_used", "operator_direct", None),
            ("inject_started", "operator_direct", None),
            ("inject_finished", "operator_direct", "dry_run"),
        ]

    def test_suite_entry_is_recorded(self, orch: ChaosOrchestrator):
        orch.run_experiment_suite([_experiment("a"), _experiment("b")])

        events = _sequence(orch)
        assert events[0] == ("hatch_used", "operator_direct", None)
        assert events.count(("inject_finished", "operator_direct", "dry_run")) == 2


class TestA4ApproveAllForce:
    def test_path_override_marks_forced_batch(self, orch: ChaosOrchestrator):
        orch.set_audit_context(path_used="cli_approve_all_force")
        orch.run_ai_experiment(
            AdvisorReport(anomalies_found=1, generated_experiments=[_experiment()])
        )
        orch.approve_and_run(0)

        paths = {path for _, path, _ in _sequence(orch)}
        assert paths == {"cli_approve_all_force"}


class TestA5RejectUnresolvableContext:
    def test_inject_blocked_without_target_context(self, orch: ChaosOrchestrator):
        orch._cg_settings = None
        orch.current_experiment = _experiment(namespace=None)
        translated = []
        orch.translator.translate = lambda exp: translated.append(exp) or []

        orch._execute_injection()

        assert translated == []  # no inject attempted
        assert orch.last_outcome == "FAIL"
        events = _sequence(orch)
        assert ("inject_started", "operator_direct", "blocked") in events
        assert not any(kind == "inject_finished" for kind, _, _ in events)

    def test_resolvable_context_is_recorded_on_inject(self, orch: ChaosOrchestrator):
        orch.run_experiment(_experiment())

        started = next(
            e for e in orch.audit_store.read_events() if e.event_type == "inject_started"
        )
        assert started.target_cluster_context is not None
        assert started.target_cluster_context.kube_namespace == "lab"
        assert started.target_cluster_context.kube_context == "kind-lab"


class TestA7ModuleDirect:
    def test_direct_module_call_emits_full_trio(self, orch: ChaosOrchestrator):
        module = orch.get_module("kubectl-chaos")
        module.execute = lambda action, params: {"success": True}

        result = orch.execute_action("kubectl-chaos", "gc_ephemeral", {})

        assert result["success"] is True
        assert _sequence(orch) == [
            ("hatch_used", "module_direct", None),
            ("inject_started", "module_direct", None),
            ("inject_finished", "module_direct", "success"),
        ]

    def test_failed_module_call_records_failure(self, orch: ChaosOrchestrator):
        module = orch.get_module("kubectl-chaos")
        module.execute = lambda action, params: {"success": False, "error": "boom"}

        orch.execute_action("kubectl-chaos", "apply_manifest", {})

        assert _sequence(orch)[-1] == ("inject_finished", "module_direct", "failure")

    def test_fsm_inject_does_not_double_emit_module_direct(
        self, orch: ChaosOrchestrator
    ):
        module = orch.get_module("kubectl-chaos")
        module.execute = lambda action, params: {"success": True}
        orch._injecting = True
        try:
            orch.execute_action("kubectl-chaos", "gc_ephemeral", {})
        finally:
            orch._injecting = False

        assert _sequence(orch) == []

    def test_missing_module_is_not_audited(self, orch: ChaosOrchestrator):
        result = orch.execute_action("nope", "gc_ephemeral", {})
        assert result["success"] is False
        assert _sequence(orch) == []


class TestInjectFailureAndRollback:
    """Every real inject closes with a terminal row plus its rollback verdict."""

    @staticmethod
    def _plan(action: str = "delete_pod"):
        from chaosgen.ucal.translator import ActionPlan

        return ActionPlan(
            tool_name="kubectl-chaos", action=action, params={"namespace": "lab"}
        )

    def test_failed_plan_records_failure_then_rollback(self, orch: ChaosOrchestrator):
        orch.translator.translate = lambda _exp: [self._plan()]
        module = orch.get_module("kubectl-chaos")
        module.execute = lambda action, params: {"success": False, "error": "denied"}

        orch.run_experiment(_experiment())

        assert orch.last_outcome == "PARTIAL"
        assert _sequence(orch) == [
            ("hatch_used", "operator_direct", None),
            ("inject_started", "operator_direct", None),
            ("inject_finished", "operator_direct", "failure"),
            ("rollback", "operator_direct", "success"),
        ]

    def test_no_target_delete_pod_is_fail_not_partial(self, orch: ChaosOrchestrator):
        """P0: zero-pod inject must not surface as PASS or generic PARTIAL."""
        orch.translator.translate = lambda _exp: [self._plan("delete_pod")]
        module = orch.get_module("kubectl-chaos")
        module.execute = lambda action, params: {
            "success": False,
            "no_target": True,
            "matched_count": 0,
            "error": "no pods matched selector, nothing injected",
        }

        orch.run_experiment(_experiment())

        assert orch.last_outcome == "NO_TARGET"
        assert ("inject_finished", "operator_direct", "failure") in _sequence(orch)

    def test_timeout_is_recorded_as_aborted(self, orch: ChaosOrchestrator):
        orch.translator.translate = lambda _exp: [self._plan()]
        module = orch.get_module("kubectl-chaos")
        module.execute = lambda action, params: {
            "success": False,
            "error": "context deadline exceeded: timeout",
        }

        orch.run_experiment(_experiment())

        assert orch.last_outcome == "INCONCLUSIVE"
        assert ("inject_finished", "operator_direct", "aborted") in _sequence(orch)

    def test_translate_error_records_failure(self, orch: ChaosOrchestrator):
        def boom(_exp):
            raise RuntimeError("no mapping for fault")

        orch.translator.translate = boom
        orch.run_experiment(_experiment())

        notes = {
            e.notes for e in orch.audit_store.read_events() if e.notes is not None
        }
        assert any("no mapping for fault" in note for note in notes)

    def test_blast_radius_violation_is_blocked_not_injected(
        self, orch: ChaosOrchestrator
    ):
        translated = []
        orch.translator.translate = lambda exp: translated.append(exp) or []
        orch.current_experiment = _experiment(namespace="kube-system")

        orch._execute_injection()

        assert translated == []
        assert orch.last_outcome == "FAIL"
        blocked = [
            e
            for e in orch.audit_store.read_events()
            if e.outcome == "blocked" and e.event_type == "inject_started"
        ]
        assert len(blocked) == 1
        assert blocked[0].blast_radius_ref is not None
        assert blocked[0].blast_radius_ref.validation_ok is False
        assert "kube-system" in blocked[0].blast_radius_ref.blocked_namespaces


class TestCtkOperatorPath:
    """CTK is the canonical runtime: an operator run must audit like A2."""

    @staticmethod
    def _stub_ctk(orchestrator: ChaosOrchestrator, result: dict) -> None:
        module = orchestrator.get_module("chaos-toolkit")
        module.execute = lambda action, params: dict(result)

        class _Verdict:
            value = "pass"

        class _Report:
            verdict = _Verdict()

        orchestrator._evaluate_ctk_run = (
            lambda run_result, *, title, description: _Report()
        )

    def test_ctk_run_emits_hatch_and_inject_pair(
        self, orch: ChaosOrchestrator, tmp_path: Path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        self._stub_ctk(orch, {"success": True, "journal_status": "completed"})

        result = orch.run_ctk_experiment(
            title="ctk-lab-run",
            faults=[
                {
                    "fault_type": "process_kill",
                    "duration": "10s",
                    "targets": [{"service": "checkout", "namespace": "lab"}],
                }
            ],
            dry_run=True,
        )

        assert result["ctk_title"] == "ctk-lab-run"
        assert _sequence(orch) == [
            ("hatch_used", "operator_direct", None),
            ("inject_started", "operator_direct", None),
            ("inject_finished", "operator_direct", "dry_run"),
        ]
        names = {e.experiment_name for e in orch.audit_store.read_events()}
        assert names == {"ctk-lab-run"}

    def test_aborted_ctk_run_records_aborted_outcome(
        self, orch: ChaosOrchestrator, tmp_path: Path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        self._stub_ctk(orch, {"success": False, "aborted": True})

        orch.run_ctk_experiment(
            title="ctk-halted",
            targets=[{"service": "checkout", "namespace": "lab"}],
            dry_run=False,
        )

        assert _sequence(orch)[-1] == ("inject_finished", "operator_direct", "aborted")

    def test_ctk_requires_targets_or_faults(self, orch: ChaosOrchestrator):
        with pytest.raises(ValueError):
            orch.run_ctk_experiment(title="empty")
        assert _sequence(orch) == []


class TestActorMissingIsNonFatal:
    def test_flow_continues_without_operator_name(self, tmp_path: Path, caplog):
        orchestrator = ChaosOrchestrator()
        orchestrator.audit_store = AuditStore(tmp_path / "audit_events.jsonl")
        orchestrator._audit_actor = None
        if orchestrator._cg_settings is not None:
            orchestrator._cg_settings.operator_name = None
        orchestrator.validator.validate = lambda _check: True
        orchestrator.translator.translate = lambda _exp: []
        orchestrator._start_dead_mans_switch = lambda: None

        with caplog.at_level("WARNING"):
            orchestrator.run_ai_experiment(
                AdvisorReport(anomalies_found=1, generated_experiments=[_experiment()])
            )

        assert orchestrator.state == "pending_approval"
        assert not orchestrator.audit_store.path.exists()
        assert any("no operator_name" in rec.message for rec in caplog.records)


class TestAuditContextAndFailures:
    def test_broken_audit_store_does_not_break_the_run(
        self, orch: ChaosOrchestrator, caplog
    ):
        class BrokenStore:
            path = Path("nowhere.jsonl")

            def emit(self, **kwargs):
                raise OSError("audit volume offline")

        orch.set_audit_context(audit_store=BrokenStore())
        with caplog.at_level("WARNING"):
            orch.run_experiment(_experiment())

        assert orch.state == "idle"
        assert any("Audit emit failed" in rec.message for rec in caplog.records)

    def test_set_audit_context_updates_fields_independently(
        self, orch: ChaosOrchestrator
    ):
        orch.set_audit_context(path_used="cli_approve_all_force")
        assert orch._audit_actor == ACTOR

        orch.set_audit_context(actor="   ")
        assert orch._audit_actor is None
        assert orch._audit_path_override == "cli_approve_all_force"

    @pytest.mark.parametrize(
        "result,expected",
        [
            ({"success": True}, "success"),
            ({"success": False}, "failure"),
            ({"success": True, "dry_run": True}, "dry_run"),
            ({"success": False, "timeout": True}, "aborted"),
            ({"success": False, "aborted": True}, "aborted"),
        ],
    )
    def test_outcome_mapping(self, result, expected):
        assert ChaosOrchestrator._outcome_from_result(result) == expected


class TestA3SkipGatekeeper:
    def test_pipeline_bypass_emits_hatch_only(self, tmp_path: Path, monkeypatch):
        from chaosgen.advisor import pipeline as pipeline_module
        from chaosgen.config.settings import ChaosGenSettings
        from chaosgen.storage import audit as audit_module

        monkeypatch.setenv(
            audit_module.AUDIT_PATH_ENV, str(tmp_path / "audit_events.jsonl")
        )
        settings = ChaosGenSettings(operator_name=ACTOR)
        settings.history.enabled = False

        report = pipeline_module.run_advisor_pipeline(
            [],
            [],
            settings=settings,
            lookback_hours=1.0,
            skip_gatekeeper=True,
            generate_chaos=False,
        )

        assert report is not None
        events = AuditStore(tmp_path / "audit_events.jsonl").read_events()
        assert [(e.event_type, e.path_used) for e in events] == [
            ("hatch_used", "skip_gatekeeper")
        ]
