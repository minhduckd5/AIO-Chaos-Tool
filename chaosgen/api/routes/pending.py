"""Pending queue list + approve / reject-all (mutating)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path

from chaosgen.api.deps import MutatingLockDep, OperatorDep, OrchestratorDep

router = APIRouter(prefix="/v1", tags=["pending"])


def _pending_summary(orch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, exp in enumerate(orch.get_pending_experiments()):
        faults = []
        for fault in getattr(exp, "faults", None) or []:
            ft = getattr(fault, "fault_type", None)
            faults.append(ft.value if hasattr(ft, "value") else str(ft))
        target = getattr(exp, "target", None)
        rows.append(
            {
                "index": index,
                "name": getattr(exp, "name", None),
                "target": getattr(target, "name", None) if target else None,
                "namespace": getattr(target, "namespace", None) if target else None,
                "fault_types": faults,
            }
        )
    return rows


@router.get("/pending")
def list_pending(orch: OrchestratorDep) -> dict[str, Any]:
    return {
        "state": orch.state,
        "pending": _pending_summary(orch),
    }


@router.post(
    "/pending/{index}/approve",
    summary="Approve pending experiment (HIGH risk — may inject)",
    description=(
        "Requires X-Operator-Name. Serialized by process mutating lock. "
        "Returns honesty dict (ran/outcome/reason). Dual-inject with GUI is an "
        "operational limit."
    ),
)
def approve_pending(
    orch: OrchestratorDep,
    lock: MutatingLockDep,
    operator: OperatorDep,
    index: int = Path(..., ge=0),
) -> dict[str, Any]:
    # --- START MODIFICATION ---
    # Critical section: bind audit actor then approve — no gap between steps.
    with lock:
        orch.set_audit_context(actor=operator, path_used="api_hitl")
        return orch.approve_and_run(index)
    # --- END MODIFICATION ---


@router.post(
    "/pending/reject-all",
    summary="Reject all pending experiments",
)
def reject_all_pending(
    orch: OrchestratorDep,
    lock: MutatingLockDep,
    operator: OperatorDep,
) -> dict[str, Any]:
    with lock:
        orch.set_audit_context(actor=operator, path_used="api_hitl")
        orch.reject_all()
        return {"ok": True, "state": orch.state}
