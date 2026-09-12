"""Unified run history resolver (Phase 2 history & audit grouping)."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from chaosgen.storage.audit import AuditEvent, AuditStore

logger = logging.getLogger(__name__)


def resolve_run_history(
    audit_store: Optional[AuditStore] = None,
    limit: int = 100,
    history_store: Any = None,
) -> List[Dict[str, Any]]:
    """
    Resolve and group audit events into coherent run records.

    Solves the PySide6 orphan `[STARTED]` bug by tracking lifecycle transitions
    by `run_id`, associating start, execution, and terminal events (PASS/FAIL/PARTIAL/HALT)
    into single unified records.
    """
    store = audit_store or AuditStore()
    events = store.read_events()

    runs_by_id: Dict[str, Dict[str, Any]] = {}
    ordered_ids: List[str] = []

    for ev in events:
        # Ignore standalone queue proposals, maintenance sweeps, or unstarted rejections
        if not ev.run_id and ev.event_type in ("queued", "orphan_sweep", "rejected"):
            continue

        run_id = ev.run_id or (f"legacy-{ev.notes}-{str(ev.timestamp)[:16]}" if ev.notes else ev.event_id)
        if run_id not in runs_by_id:
            ordered_ids.append(run_id)
            target_ns = None
            target_svc = None
            if ev.target_cluster_context:
                target_ns = getattr(ev.target_cluster_context, "kube_namespace", None) or getattr(ev.target_cluster_context, "namespace", None)
                target_svc = getattr(ev.target_cluster_context, "service_target", None)

            runs_by_id[run_id] = {
                "run_id": run_id,
                "name": ev.experiment_name or ev.notes or "unnamed-experiment",
                "actor": ev.actor,
                "path_used": ev.path_used,
                "started_at": ev.timestamp,
                "finished_at": None,
                "status": "RUNNING",
                "outcome": None,
                "target_service": target_svc,
                "target_namespace": target_ns,
                "notes": ev.notes,
                "duration_seconds": None,
                "events_count": 0,
            }

        rec = runs_by_id[run_id]
        rec["events_count"] += 1
        if ev.experiment_name and rec["name"] in ("unnamed-experiment", ev.notes):
            rec["name"] = ev.experiment_name
        elif ev.notes and rec["name"] == "unnamed-experiment":
            rec["name"] = ev.notes
        if ev.actor and not rec.get("actor"):
            rec["actor"] = ev.actor
        if ev.target_cluster_context:
            if not rec.get("target_namespace"):
                rec["target_namespace"] = getattr(ev.target_cluster_context, "kube_namespace", None) or getattr(ev.target_cluster_context, "namespace", None)
            if not rec.get("target_service"):
                rec["target_service"] = getattr(ev.target_cluster_context, "service_target", None)

        # Process terminal / lifecycle events
        ev_type = ev.event_type
        if ev_type in ("inject_started", "hatch_used", "started"):
            if not rec.get("started_at") or ev_type in ("inject_started", "hatch_used"):
                rec["started_at"] = ev.timestamp

        elif ev_type in ("inject_finished", "finished"):
            rec["finished_at"] = ev.timestamp
            outcome = (ev.outcome or "").upper()
            if outcome == "SUCCESS":
                rec["status"] = "PASSED"
                rec["outcome"] = "PASS"
            elif outcome == "FAILURE":
                rec["status"] = "FAILED"
                rec["outcome"] = "FAIL"
            elif outcome in ("ABORTED", "PARTIAL") or "partial" in str(ev.notes or "").lower():
                rec["status"] = "PARTIAL"
                rec["outcome"] = "PARTIAL"
            elif outcome == "DRY_RUN":
                rec["status"] = "DRY_RUN"
                rec["outcome"] = "DRY_RUN"
            else:
                rec["status"] = outcome or "UNKNOWN"
                rec["outcome"] = outcome or None
            if ev.notes:
                rec["notes"] = ev.notes

        elif ev_type == "rollback":
            if rec["status"] in ("RUNNING", "INTERRUPTED"):
                rec["finished_at"] = ev.timestamp
                rec["status"] = "ROLLBACK"
                rec["outcome"] = "FAIL"

        elif ev_type == "rejected":
            rec["finished_at"] = ev.timestamp
            rec["status"] = "REJECTED"
            rec["outcome"] = None
            if ev.notes:
                rec["notes"] = ev.notes

    # Compute duration_seconds and eliminate orphan RUNNING states for inactive past runs
    for rec in runs_by_id.values():
        if rec.get("finished_at") is None and rec.get("status") == "RUNNING":
            rec["status"] = "INTERRUPTED"
            rec["outcome"] = "PARTIAL"

        if rec.get("started_at") and rec.get("finished_at"):
            try:
                t0 = datetime.fromisoformat(str(rec["started_at"]).replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(str(rec["finished_at"]).replace("Z", "+00:00"))
                rec["duration_seconds"] = max(0.0, round((t1 - t0).total_seconds(), 2))
            except Exception:
                rec["duration_seconds"] = None

    # Sort descending by started_at
    resolved = [runs_by_id[rid] for rid in ordered_ids]
    resolved.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    return resolved[:limit]
