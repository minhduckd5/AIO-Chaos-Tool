"""Phase B — multi-service experiment suite (cap, abort, deferred cleanup)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chaosgen.orchestrator import ChaosOrchestrator
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    ProcessFaultSpec,
    TargetSpec,
    TargetType,
)


def _exp(name: str, service: str) -> ChaosExperiment:
    return ChaosExperiment(
        name=name,
        description="suite test",
        target=TargetSpec(
            type=TargetType.SERVICE,
            name=service,
            namespace="default",
            selector={"app": service},
        ),
        faults=[
            ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="10s"),
        ],
    )


def _orch_with_cap(max_n: int = 3) -> ChaosOrchestrator:
    orch = ChaosOrchestrator()
    orch._cg_settings = SimpleNamespace(
        safety=SimpleNamespace(max_services_per_suite=max_n),
    )
    return orch


def test_suite_rejects_over_cap():
    orch = _orch_with_cap(2)
    experiments = [_exp(f"e-{i}", f"svc-{i}") for i in range(3)]
    with pytest.raises(ValueError, match="max_services_per_suite"):
        orch.run_experiment_suite(experiments)


def test_suite_defers_cleanup_until_end(monkeypatch):
    orch = _orch_with_cap(3)
    calls: list[str] = []

    def fake_run(exp):
        orch.state = "idle"
        orch.last_outcome = "PASS"
        orch.suite_manifests.append(
            {
                "path": f"/tmp/{exp.target.name}.yaml",
                "kind": "NetworkChaos",
                "name": exp.name,
                "namespace": "default",
            }
        )
        calls.append(exp.target.name)

    rollbacks: list[int] = []

    def fake_rollback(*, best_effort=False):
        rollbacks.append(len(orch.active_manifests))
        orch.last_rollback_status = "pass"
        orch.active_manifests = []
        return "pass"

    monkeypatch.setattr(orch, "run_experiment", fake_run)
    monkeypatch.setattr(orch, "_rollback_manifests", fake_rollback)

    result = orch.run_experiment_suite(
        [_exp("a", "frontend"), _exp("b", "checkout")],
        delay_seconds=0,
    )

    assert calls == ["frontend", "checkout"]
    assert len(rollbacks) == 1
    assert rollbacks[0] == 2
    assert result["outcome"] == "PASS"
    assert len(result["results"]) == 2
    assert orch._suite_mode is False
    assert orch.suite_manifests == []


def test_suite_aborts_on_inconclusive(monkeypatch):
    orch = _orch_with_cap(3)
    seen: list[str] = []

    def fake_run(exp):
        orch.state = "idle"
        seen.append(exp.target.name)
        orch.last_outcome = "INCONCLUSIVE" if exp.target.name == "mid" else "PASS"

    monkeypatch.setattr(orch, "run_experiment", fake_run)
    monkeypatch.setattr(
        orch,
        "_rollback_manifests",
        MagicMock(return_value="pass"),
    )

    result = orch.run_experiment_suite(
        [
            _exp("1", "first"),
            _exp("2", "mid"),
            _exp("3", "last"),
        ]
    )

    assert seen == ["first", "mid"]
    assert result["outcome"] == "INCONCLUSIVE"
    assert len(result["results"]) == 2
