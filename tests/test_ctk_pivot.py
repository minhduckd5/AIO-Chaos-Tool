"""CTK schema + builder + module unit tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chaosgen.gui.ctk_form import build_experiment_intent, intent_to_json
from chaosgen.modules.chaos_toolkit import ChaosToolkitModule, _kill_process_tree
from chaosgen.schemas.chaos_intent import CtkExperimentIntent, CtkFaultIntent, CtkTargetRef
from chaosgen.schemas.ctk_experiment import CtkExperiment
from chaosgen.ucal.ctk_builder import CtkBuildIntent, CtkTargetIntent, build_experiment, build_experiment_from_intent


SAMPLE = {
    "title": "Kill shipping",
    "description": "terminate shippingservice",
    "tags": ["chaosgen"],
    "method": [
        {
            "type": "action",
            "name": "terminate-shipping",
            "provider": {
                "type": "python",
                "module": "chaosk8s.pod.actions",
                "func": "terminate_pods",
                "arguments": {
                    "label_selector": "app=shippingservice",
                    "ns": "default",
                },
            },
        }
    ],
}


def test_ctk_schema_round_trip():
    exp = CtkExperiment.model_validate(SAMPLE)
    dumped = exp.to_ctk_dict()
    assert dumped["title"] == "Kill shipping"
    assert len(dumped["method"]) == 1
    again = CtkExperiment.model_validate(dumped)
    assert again.method[0].name == "terminate-shipping"


def test_ctk_schema_requires_method():
    with pytest.raises(Exception):
        CtkExperiment.model_validate(
            {"title": "x", "description": "y", "method": []}
        )


def test_builder_multi_service_terminate():
    exp = build_experiment(
        CtkBuildIntent(
            title="multi-kill",
            targets=[
                CtkTargetIntent(service="frontend"),
                CtkTargetIntent(service="checkoutservice"),
                CtkTargetIntent(service="shippingservice"),
            ],
            max_actions=3,
        )
    )
    assert len(exp.method) == 3
    mods = [a.provider["module"] for a in exp.method]
    assert all(m == "chaosk8s.pod.actions" for m in mods)


def test_builder_network_uses_chaosmesh_module():
    exp = build_experiment(
        CtkBuildIntent(
            title="net",
            targets=[
                CtkTargetIntent(
                    service="frontend",
                    fault_type="network_latency",
                    latency="200ms",
                )
            ],
        )
    )
    assert exp.method[0].provider["module"] == "chaosk8s.chaosmesh.network.actions"
    assert exp.method[0].provider["func"] == "add_latency"


def test_builder_respects_cap_and_blocked_ns():
    with pytest.raises(ValueError, match="max_actions"):
        build_experiment(
            CtkBuildIntent(
                title="too-many",
                targets=[CtkTargetIntent(service=f"s{i}") for i in range(4)],
                max_actions=3,
            )
        )
    with pytest.raises(ValueError, match="blocked"):
        build_experiment(
            CtkBuildIntent(
                title="blocked",
                targets=[CtkTargetIntent(service="coredns", namespace="kube-system")],
            )
        )


def test_chaos_toolkit_dry_run_validate(tmp_path):
    path = tmp_path / "exp.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    mod = ChaosToolkitModule({"dry_run": True, "timeout_s": 30})
    with patch.object(mod, "_run_cmd") as run:
        run.return_value = {
            "success": True,
            "module": "chaos-toolkit",
            "message": "ok",
            "cmd": ["chaos", "validate", str(path)],
        }
        result = mod.execute("run_experiment", {"experiment_file": str(path), "dry_run": True})
    assert result["success"] is True
    assert result["dry_run"] is True
    assert run.call_args[0][0][0] == "validate"


def test_chaos_toolkit_run_passes_journal(tmp_path):
    path = tmp_path / "exp.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    journal = tmp_path / "j.json"
    journal.write_text(json.dumps({"status": "completed", "deviated": False}), encoding="utf-8")
    mod = ChaosToolkitModule({"dry_run": False, "timeout_s": 30})
    with patch.object(mod, "_run_cmd_long") as run:
        run.return_value = {
            "success": True,
            "module": "chaos-toolkit",
            "message": "ok",
            "cmd": ["chaos", "run", str(path)],
        }
        result = mod.execute(
            "run_experiment",
            {"experiment_file": str(path), "dry_run": False, "journal_path": str(journal)},
        )
    assert result["success"] is True
    assert result.get("journal_status") == "completed"
    assert "run" in run.call_args[0][0]


def test_builder_action_pause_on_second_target():
    intent = CtkExperimentIntent(
        title="pause",
        faults=[
            CtkFaultIntent(
                fault_type="process_kill",
                targets=[
                    CtkTargetRef(service="a"),
                    CtkTargetRef(service="b"),
                ],
            )
        ],
        action_pause_seconds=5,
        max_actions=3,
    )
    exp = build_experiment_from_intent(intent)
    assert exp.method[1].pauses == {"after": "5s"}


def test_builder_network_rollbacks_when_enabled():
    intent = CtkExperimentIntent(
        title="net",
        faults=[
            CtkFaultIntent(
                fault_type="network_latency",
                targets=[CtkTargetRef(service="frontend")],
            )
        ],
        auto_rollback=True,
        max_actions=3,
    )
    exp = build_experiment_from_intent(intent)
    assert exp.rollbacks and len(exp.rollbacks) == 1
    assert exp.rollbacks[0].provider["func"] == "delete_network_fault"


def test_ctk_form_build_fault_rows():
    intent = build_experiment_intent(
        title="t",
        description="d",
        fault_rows=[
            {
                "ftype": "process_kill",
                "duration": "30s",
                "latency": "100ms",
                "loss_percentage": 10,
                "signal": "SIGKILL",
                "services": ["frontend", "checkout"],
            }
        ],
        action_pause_seconds=2,
        max_actions=3,
    )
    dumped = json.loads(intent_to_json(intent))
    assert len(dumped["method"]) == 2


def test_chaos_toolkit_abort_run_kills_active_proc():
    mod = ChaosToolkitModule({"timeout_s": 30})
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 12345
    mod._proc = proc
    with patch("chaosgen.modules.chaos_toolkit._kill_process_tree") as kill:
        aborted = mod.abort_run()
    assert aborted is True
    kill.assert_called_once_with(12345)
