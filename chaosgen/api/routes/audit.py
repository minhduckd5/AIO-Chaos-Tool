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
    if limit < len(events):
        events = events[-limit:]
    return {
        "count": len(events),
        "events": [e.model_dump(mode="json") for e in events],
    }
