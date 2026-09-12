"""HALT / abort active experiment (mutating)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from chaosgen.api.deps import MutatingLockDep, OperatorDep, OrchestratorDep

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
