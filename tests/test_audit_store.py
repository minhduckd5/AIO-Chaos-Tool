"""Audit trail store: schema v1, actor resolution (A8), mirror failure mode, A9."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from chaosgen.config.settings import ChaosGenSettings, load_settings
from chaosgen.storage import atomic_io, audit as audit_module
from chaosgen.storage.audit import (
    AuditActorRequired,
    AuditStore,
    TargetClusterContext,
    criteria_ref_for_path,
    emit_best_effort,
    resolve_actor,
)


def _store(tmp_path: Path, **kwargs) -> AuditStore:
    return AuditStore(tmp_path / "audit_events.jsonl", **kwargs)


class TestEmit:
    def test_emit_appends_schema_v1_row(self, tmp_path: Path):
        store = _store(tmp_path, actor="sre-anh")
        event = store.emit(
            event_type="approved",
            path_used="ai_hitl",
            run_id="run-1",
            experiment_name="checkout-latency",
        )

        rows = [json.loads(line) for line in store.path.read_text("utf-8").splitlines()]
        assert len(rows) == 1
        assert rows[0]["schema_version"] == 1
        assert rows[0]["event_id"] == event.event_id
        assert rows[0]["actor"] == "sre-anh"
        assert rows[0]["event_type"] == "approved"
        assert rows[0]["path_used"] == "ai_hitl"
        assert rows[0]["run_id"] == "run-1"

    def test_emit_is_append_only(self, tmp_path: Path):
        store = _store(tmp_path, actor="sre-anh")
        store.emit(event_type="hatch_used", path_used="operator_direct")
        store.emit(
            event_type="inject_started",
            path_used="operator_direct",
            target_cluster_context=TargetClusterContext(kube_namespace="lab"),
        )
        store.emit(
            event_type="inject_finished", path_used="operator_direct", outcome="success"
        )

        events = store.read_events()
        assert [e.event_type for e in events] == [
            "hatch_used",
            "inject_started",
            "inject_finished",
        ]

    def test_emit_without_actor_raises(self, tmp_path: Path):
        store = _store(tmp_path)
        with pytest.raises(AuditActorRequired):
            store.emit(event_type="approved", path_used="ai_hitl")
        assert not store.path.exists()

    def test_unknown_enum_value_rejected(self, tmp_path: Path):
        store = _store(tmp_path, actor="sre-anh")
        with pytest.raises(Exception):
            store.emit(event_type="approved", path_used="execute_action")

    def test_jsonl_write_failure_is_not_swallowed(self, tmp_path: Path, monkeypatch):
        """JSONL is the SOT: a failed append must fail the emit, not warn."""
        store = _store(tmp_path, actor="sre-anh")
        monkeypatch.setattr(
            audit_module,
            "append_jsonl_line",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(OSError):
            store.emit(event_type="approved", path_used="ai_hitl")

    def test_iter_events_skips_invalid_rows(self, tmp_path: Path):
        store = _store(tmp_path, actor="sre-anh")
        store.emit(event_type="approved", path_used="ai_hitl")
        with store.path.open("a", encoding="utf-8") as handle:
            handle.write('{"schema_version": 1, "event_type": "nonsense"}\n')
        store.emit(event_type="rejected", path_used="ai_hitl")

        assert [e.event_type for e in store.read_events()] == ["approved", "rejected"]


class TestMirrorFailureMode:
    class _BrokenMirror:
        def record_audit_event(self, record):
            raise RuntimeError("database is locked")

    def test_mirror_failure_keeps_jsonl_and_does_not_raise(
        self, tmp_path: Path, caplog
    ):
        store = _store(tmp_path, actor="sre-anh", history_store=self._BrokenMirror())
        with caplog.at_level("WARNING"):
            store.emit(event_type="approved", path_used="ai_hitl")

        assert len(store.read_events()) == 1
        assert any("mirror failed" in rec.message for rec in caplog.records)

    def test_mirror_receives_record(self, tmp_path: Path):
        seen = []

        class Mirror:
            def record_audit_event(self, record):
                seen.append(record)

        store = _store(tmp_path, actor="sre-anh", history_store=Mirror())
        store.emit(event_type="approved", path_used="ai_hitl")
        assert seen and seen[0]["event_type"] == "approved"

    def test_sqlite_mirror_roundtrip(self, tmp_path: Path):
        from chaosgen.storage.history import HistoryStore

        history = HistoryStore(tmp_path / "history.db", async_writes=False)
        store = _store(tmp_path, actor="sre-anh", history_store=history)
        store.emit(
            event_type="inject_started",
            path_used="operator_direct",
            criteria_ref=criteria_ref_for_path(_write_criteria(tmp_path)),
            target_cluster_context=TargetClusterContext(kube_namespace="lab"),
        )

        mirrored = history.audit_events()
        assert len(mirrored) == 1
        assert mirrored[0]["path_used"] == "operator_direct"
        assert mirrored[0]["criteria_sha256"]


def _write_criteria(tmp_path: Path) -> Path:
    path = tmp_path / "criteria.yaml"
    path.write_text("claim: checkout stays healthy\n", encoding="utf-8")
    return path


class TestStoreConfiguration:
    def test_default_path_follows_env_override(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "audit_events.jsonl"
        monkeypatch.setenv(audit_module.AUDIT_PATH_ENV, str(target))
        store = AuditStore()
        assert store.path == target

        store.set_actor("sre-anh")
        store.emit(event_type="approved", path_used="ai_hitl")
        assert store.actor == "sre-anh"
        assert target.is_file()

    def test_default_path_without_override(self, monkeypatch):
        monkeypatch.delenv(audit_module.AUDIT_PATH_ENV, raising=False)
        assert audit_module.default_audit_path().name == audit_module.AUDIT_FILENAME

    def test_set_actor_clears_on_blank(self, tmp_path):
        store = _store(tmp_path, actor="sre-anh")
        store.set_actor("   ")
        assert store.actor is None

    def test_mirror_without_recorder_is_skipped_once(self, tmp_path, caplog):
        class NotAMirror:
            pass

        store = _store(tmp_path, actor="sre-anh", history_store=NotAMirror())
        with caplog.at_level("WARNING"):
            store.emit(event_type="approved", path_used="ai_hitl")
            store.emit(event_type="rejected", path_used="ai_hitl")

        warnings = [r for r in caplog.records if "cannot mirror" in r.message]
        assert len(warnings) == 1
        assert len(store.read_events()) == 2

    def test_criteria_ref_missing_file_is_none(self, tmp_path):
        assert criteria_ref_for_path(tmp_path / "absent.yaml") is None


class TestActorResolution:
    def test_configured_operator_is_used(self):
        settings = ChaosGenSettings(operator_name="sre-anh")
        assert resolve_actor(settings=settings) == "sre-anh"

    def test_prompt_persists_operator_name(self, tmp_path: Path):
        settings_path = tmp_path / "settings.yaml"
        settings_path.write_text("llm_provider: ollama\n", encoding="utf-8")

        actor = resolve_actor(
            prompt=lambda: "sre-anh", settings_path=str(settings_path)
        )
        assert actor == "sre-anh"
        assert load_settings(str(settings_path)).operator_name == "sre-anh"

    def test_empty_prompt_raises_never_defaults(self, tmp_path: Path):
        settings_path = tmp_path / "settings.yaml"
        settings_path.write_text("llm_provider: ollama\n", encoding="utf-8")

        with pytest.raises(AuditActorRequired):
            resolve_actor(prompt=lambda: "   ", settings_path=str(settings_path))
        with pytest.raises(AuditActorRequired):
            resolve_actor(prompt=lambda: None, settings_path=str(settings_path))
        assert load_settings(str(settings_path)).operator_name is None

    def test_no_prompt_and_no_config_raises(self):
        with pytest.raises(AuditActorRequired):
            resolve_actor(settings=ChaosGenSettings())

    def test_emit_best_effort_drops_event_without_actor(self, caplog):
        with caplog.at_level("WARNING"):
            event = emit_best_effort(
                event_type="hatch_used",
                path_used="skip_gatekeeper",
                settings=ChaosGenSettings(),
            )
        assert event is None
        assert any("no operator_name" in rec.message for rec in caplog.records)

    def test_emit_best_effort_writes_with_configured_actor(self, tmp_path, monkeypatch):
        target = tmp_path / "audit_events.jsonl"
        monkeypatch.setenv(audit_module.AUDIT_PATH_ENV, str(target))
        event = emit_best_effort(
            event_type="hatch_used",
            path_used="skip_gatekeeper",
            settings=ChaosGenSettings(operator_name="sre-anh"),
        )
        assert event is not None
        assert target.exists()


class TestTargetClusterContext:
    def test_blank_context_is_unresolvable(self):
        assert not TargetClusterContext().is_resolvable()
        assert not TargetClusterContext(kube_namespace="  ").is_resolvable()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"kube_context": "kind-lab"},
            {"kube_namespace": "lab"},
            {"docker_host": "tcp://127.0.0.1:2375"},
            {"environment_hint": "kubernetes"},
        ],
    )
    def test_any_field_resolves(self, kwargs):
        assert TargetClusterContext(**kwargs).is_resolvable()


class TestNoReplaceInAuditPath:
    """
    Guard: the evidence trail must never adopt PromotedStore's replace path.

    ``os.replace`` on the live JSONL is what dropped PromotedStore records under
    Windows concurrency; this test fails CI if it is wired into audit later.
    """

    def _calls(self, source: str) -> set[str]:
        tree = ast.parse(source)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
        return names

    def test_append_jsonl_line_never_replaces(self):
        calls = self._calls(
            inspect.getsource(atomic_io.append_jsonl_line).lstrip()
        )
        assert "replace_with_retry" not in calls
        assert "replace" not in calls

    def test_audit_module_never_replaces(self):
        source = Path(audit_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "replace_with_retry" not in imported
        assert "append_jsonl_line" in imported

        calls = self._calls(source)
        assert "replace_with_retry" not in calls
        assert "replace" not in calls
        assert "append_jsonl_line" in calls
