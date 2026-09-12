"""Experiment execution and history routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from chaosgen.api.deps import MutatingLockDep, OperatorDep, OrchestratorDep
from chaosgen.schemas.scenarios import ChaosExperiment
from chaosgen.storage.run_resolver import resolve_run_history

router = APIRouter(prefix="/v1", tags=["experiments"])


@router.get(
    "/experiments/history",
    summary="List experiment run history with terminal outcomes",
    description=(
        "Returns aggregated run history resolved from audit records and history store, "
        "eliminating orphaned [STARTED] statuses."
    ),
)
def get_experiment_history(
    orch: OrchestratorDep = None,
) -> dict[str, Any]:
    audit_store = getattr(orch, "audit_store", None)
    history_store = getattr(orch, "history_store", None)
    runs = resolve_run_history(audit_store=audit_store, history_store=history_store)
    return {
        "count": len(runs),
        "runs": runs,
    }


@router.post(
    "/experiments/run",
    summary="Execute experiment via direct operator hatch (mutating)",
    description=(
        "Executes a single experiment directly. Pre-validates blast radius policies, "
        "enforces idle state (409 if active), and emits an audit event marking operator_direct path."
    ),
)
def run_direct_experiment(
    experiment: ChaosExperiment,
    orch: OrchestratorDep = None,
    lock: MutatingLockDep = None,
    operator: OperatorDep = None,
) -> dict[str, Any]:
    # 409 guard: only allow run from idle
    if orch.state != "idle":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot run experiment: orchestrator is not idle (current state: '{orch.state}')",
        )

    # Pre-validate blast radius (422 if violation)
    controller = getattr(orch, "blast_radius_controller", None)
    if controller is not None:
        try:
            controller.validate_experiment(experiment)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Blast radius validation failed: {exc}",
            )

    with lock:
        orch.set_audit_context(actor=operator, path_used="operator_direct")
        result = orch.run_experiment(experiment)
        return {
            "status": "started",
            "run_id": getattr(orch, "_run_id", None),
            "result": result,
        }
