"""Recent audit events — reuses AuditStore / iter_jsonl (A9)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from chaosgen.storage.audit import AuditStore

router = APIRouter(prefix="/v1", tags=["audit"])


@router.get("/audit/recent")
def audit_recent(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    store = AuditStore()
    events = store.read_events()
    events.sort(key=lambda e: str(e.timestamp), reverse=True)
    if limit < len(events):
        events = events[:limit]
    return {
        "count": len(events),
        "events": [e.model_dump(mode="json") for e in events],
    }


@router.get(
    "/audit/summary",
    summary="Counted audit summary for filter tabs",
    description="Aggregates audit events by outcome for Versus-style counted filter tabs.",
)
def audit_summary() -> dict[str, int]:
    store = AuditStore()
    events = store.read_events()

    pass_count = 0
    fail_count = 0
    partial_count = 0
    no_target_count = 0
    inconclusive_count = 0

    for ev in events:
        outcome = (ev.outcome or "").lower()
        notes = (ev.notes or "").lower()

        if "no_target" in notes:
            no_target_count += 1
        elif "inconclusive" in notes:
            inconclusive_count += 1
        elif outcome == "success" or "pass" in notes:
            pass_count += 1
        elif outcome == "failure" or "fail" in notes:
            fail_count += 1
        elif outcome in ("aborted", "partial") or "partial" in notes:
            partial_count += 1

    return {
        "all": len(events),
        "total": len(events),
        "pass": pass_count,
        "fail": fail_count,
        "partial": partial_count,
        "no_target": no_target_count,
        "inconclusive": inconclusive_count,
    }
