"""Pending queue list + approve / reject-all (mutating)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path

from chaosgen.api.deps import MutatingLockDep, OperatorDep, OrchestratorDep

router = APIRouter(prefix="/v1", tags=["pending"])


def _pending_summary(orch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    report = getattr(orch, "pending_report", None)
    sci_scores = getattr(report, "sci_scores", None) or []
    hypotheses = getattr(report, "hypotheses", None) or []
    anomalies_found = getattr(report, "anomalies_found", 0)
    consumed_approvals = getattr(orch, "_consumed_approvals", set())

    for index, exp in enumerate(orch.get_pending_experiments()):
        faults = []
        for fault in getattr(exp, "faults", None) or []:
            ft = getattr(fault, "fault_type", None)
            faults.append(ft.value if hasattr(ft, "value") else str(ft))
        target = getattr(exp, "target", None)
        sci = sci_scores[index] if index < len(sci_scores) else None
        hyp = hypotheses[index] if index < len(hypotheses) else None

        name = getattr(exp, "name", "") or f"experiment-{index}"
        is_catalog = (anomalies_found == 0 and not name.startswith("ai-"))
        origin = "catalog" if is_catalog else "ai_advisor"

        rows.append(
            {
                "index": index,
                "name": name,
                "target": getattr(target, "name", None) if target else None,
                "namespace": getattr(target, "namespace", None) if target else None,
                "fault_types": faults,
                "description": getattr(exp, "description", None),
                "origin": origin,
                "confidence": getattr(hyp, "confidence", None) if hyp else None,
                "hypothesis": getattr(hyp, "rationale", None) if hyp else getattr(exp, "description", None),
                "sci_score": getattr(sci, "weighted_score", None) if sci else None,
                "consumed": name in consumed_approvals,
            }
        )
    return rows


@router.get("/pending")
def list_pending(orch: OrchestratorDep) -> dict[str, Any]:
    rows = _pending_summary(orch)
    return {
        "state": orch.state,
        "count": len(rows),
        "pending": rows,
    }


@router.post(
    "/pending/{index}/approve",
    summary="Approve pending experiment (HIGH risk — may inject)",
    description=(
        "Requires X-Operator-Name. Serialized by process mutating lock. "
        "Returns honesty dict (ran/outcome/reason). Dual-inject with GUI is an "
        "operational limit.\n\n"
        "Outcome semantics:\n"
        "- outcome='PARTIAL' indicates that injection aborted midway (e.g. missing "
        "cluster CRD, unsupported fault type, or runner error) or that post-fault "
        "verification measured partial resilience. To disambiguate, automated callers "
        "should query GET /v1/audit/recent to inspect 'notes' on the inject_finished event."
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


@router.post(
    "/pending/{index}/reject",
    summary="Reject single pending experiment",
)
@router.delete(
    "/pending/{index}",
    summary="Reject single pending experiment (DELETE synonym)",
)
def reject_single_pending(
    orch: OrchestratorDep,
    lock: MutatingLockDep,
    operator: OperatorDep,
    index: int = Path(..., ge=0),
) -> dict[str, Any]:
    with lock:
        orch.set_audit_context(actor=operator, path_used="api_hitl")
        ok = orch.reject_pending_at(index)
        if not ok:
            return {
                "ok": False,
                "reason": f"invalid experiment index {index} or empty queue",
                "state": orch.state,
            }
        return {
            "ok": True,
            "state": orch.state,
            "remaining": len(orch.get_pending_experiments()),
        }
