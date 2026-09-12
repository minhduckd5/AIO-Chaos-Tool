"""HALT / abort active experiment and resource maintenance (mutating)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from chaosgen.api.deps import MutatingLockDep, OperatorDep, OrchestratorDep
from chaosgen.storage.audit import AuditStore
from chaosgen.storage.orphan_store import sweep_orphans

router = APIRouter(prefix="/v1", tags=["control"])


@router.post(
    "/halt",
    summary="Halt active experiment",
    description=(
        "Requires X-Operator-Name. Serialized by process mutating lock. "
        "Dual-inject with GUI is an operational limit."
    ),
)
def halt(
    orch: OrchestratorDep,
    lock: MutatingLockDep,
    operator: OperatorDep,
) -> dict[str, Any]:
    with lock:
        orch.set_audit_context(actor=operator, path_used="api_hitl")
        return orch.halt_active_experiment()


@router.post(
    "/orphan-sweep",
    summary="Sweep and clean orphaned chaos resources",
    description="Removes expired tracked orphan resource records and emits an audit event.",
)
@router.post(
    "/control/orphan-sweep",
    include_in_schema=False,
)
def orphan_sweep(
    older_than_seconds: int = Query(3600, ge=0, description="Age threshold in seconds"),
    orch: OrchestratorDep = None,
    lock: MutatingLockDep = None,
    operator: OperatorDep = None,
) -> dict[str, Any]:
    with lock:
        orch.set_audit_context(actor=operator, path_used="api_hitl")
        removed = sweep_orphans(older_than_seconds=older_than_seconds)

        store = getattr(orch, "audit_store", None) or AuditStore()
        notes = f"Swept {len(removed)} orphan resource(s) older than {older_than_seconds}s"
        store.emit(
            actor=operator,
            event_type="orphan_sweep",
            path_used="api_hitl",
            notes=notes,
        )

        return {
            "swept": True,
            "removed_count": len(removed),
            "removed": removed,
        }
