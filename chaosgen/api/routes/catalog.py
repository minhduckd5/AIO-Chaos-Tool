"""Scenario catalog routes: list catalog entries and stage into approval queue."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from chaosgen.advisor.scenario_catalog import ScenarioCatalog
from chaosgen.api.deps import MutatingLockDep, OperatorDep, OrchestratorDep
from chaosgen.schemas.faults import TargetSpec, TargetType
from chaosgen.schemas.scenarios import AdvisorReport

router = APIRouter(prefix="/v1", tags=["catalog"])


class QueueTargetOverride(BaseModel):
    target: Optional[str] = None
    namespace: Optional[str] = None


@router.get(
    "/catalog",
    summary="List all catalog scenarios",
    description="Returns builtin and promoted scenarios indexed by architecture and fault type.",
)
def list_catalog() -> dict[str, Any]:
    catalog = ScenarioCatalog()
    entries = catalog.iter_all()
    results = []
    for e in entries:
        results.append(
            {
                "name": e.name,
                "description": e.description,
                "architecture": e.architecture.value if hasattr(e.architecture, "value") else str(e.architecture),
                "fault_type": e.fault_type.value if hasattr(e.fault_type, "value") else str(e.fault_type),
                "tags": list(e.tags or []),
                "source": getattr(e, "source", "builtin"),
                "acceptance_criteria": getattr(e, "acceptance_criteria", None),
            }
        )
    return {
        "count": len(results),
        "scenarios": results,
    }


@router.post(
    "/catalog/{name}/queue",
    summary="Queue a catalog scenario for approval (mutating)",
    description=(
        "Stages a catalog scenario into the HITL pending queue. "
        "Returns 409 Conflict if unapproved scenarios exist unless force=true is passed."
    ),
)
def queue_catalog_scenario(
    name: str = Path(..., description="Catalog scenario name"),
    force: bool = Query(False, description="Force replacement of unapproved pending scenarios"),
    override: Optional[QueueTargetOverride] = None,
    orch: OrchestratorDep = None,
    lock: MutatingLockDep = None,
    operator: OperatorDep = None,
) -> dict[str, Any]:
    catalog = ScenarioCatalog()
    def _matches(e, target_name: str) -> bool:
        if e.name == target_name or e.name.lower() == target_name.lower():
            return True
        try:
            built_name = getattr(e.build(), "name", "")
            if built_name == target_name or built_name.lower() == target_name.lower():
                return True
        except Exception:
            pass
        return False

    matches = [e for e in catalog.iter_all() if _matches(e, name)]
    if not matches:
        raise HTTPException(status_code=404, detail=f"Catalog scenario '{name}' not found")

    entry = matches[0]

    with lock:
        # 409 Guard against silent overwrite of unapproved queue
        if orch.pending_experiments and not force:
            dropped = [getattr(e, "name", str(e)) for e in orch.pending_experiments]
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Queue contains {len(dropped)} unapproved scenario(s): {', '.join(dropped)}. "
                    "Pass ?force=true to replace."
                ),
            )

        dropped_count = len(orch.pending_experiments) if orch.pending_experiments else 0
        orch.set_audit_context(actor=operator, path_used="api_hitl")

        exp = entry.build()
        if override:
            if override.target:
                current_target = getattr(exp, "target", None)
                ttype = getattr(current_target, "type", TargetType.SERVICE) if current_target else TargetType.SERVICE
                ns = override.namespace or (getattr(current_target, "namespace", "default") if current_target else "default")
                exp.target = TargetSpec(type=ttype, name=override.target, namespace=ns)
            elif override.namespace and getattr(exp, "target", None):
                exp.target.namespace = override.namespace

        report = AdvisorReport(
            anomalies_found=0,
            generated_experiments=[exp],
        )
        orch.run_ai_experiment(report)

        return {
            "queued": True,
            "name": exp.name,
            "target": getattr(getattr(exp, "target", None), "name", None),
            "namespace": getattr(getattr(exp, "target", None), "namespace", None),
            "replaced_unapproved_count": dropped_count,
        }
