"""Telemetry readiness, live connectivity check, and anomaly analysis routes."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from chaosgen.api.deps import MutatingLockDep, OperatorDep, OrchestratorDep
from chaosgen.config.settings import load_settings
from chaosgen.config.telemetry_endpoints import (
    resolve_loki_url,
    resolve_prometheus_url,
)
from chaosgen.gui.analysis_pipeline import AnalysisRequest, run_gui_analysis_pipeline
from chaosgen.ingestion.telemetry_factory import check_live_stack
from chaosgen.schemas.discovery import ObservabilityTool

router = APIRouter(prefix="/v1", tags=["telemetry"])


class TelemetryCheckRequest(BaseModel):
    prom_url: Optional[str] = None
    loki_url: Optional[str] = None


class TelemetryAnalyzeRequest(BaseModel):
    source: Literal["live", "export"] = "live"
    lookback_hours: int = Field(24, ge=1, le=168)
    export_path: Optional[str] = None
    clustering_mode: str = "auto"
    n_clusters: int = Field(5, ge=1, le=20)
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    skip_gatekeeper: bool = False


@router.get(
    "/telemetry/ready",
    summary="Check configured telemetry endpoints",
    description="Inspects saved settings to report if Prometheus and Loki URLs are configured.",
)
def telemetry_ready() -> dict[str, Any]:
    settings = load_settings()
    hints = list(getattr(getattr(settings, "hints", None), "observability", None) or [])
    prom = False
    loki = False
    for hint in hints:
        tool = getattr(hint, "tool", None)
        url = (getattr(hint, "url", None) or "").strip()
        if not url:
            continue
        if tool == ObservabilityTool.PROMETHEUS or str(tool).lower() == "prometheus":
            prom = True
        if tool == ObservabilityTool.LOKI or str(tool).lower() == "loki":
            loki = True
    return {
        "prometheus_configured": prom,
        "loki_configured": loki,
        "ready": prom or loki,
    }


@router.post(
    "/telemetry/check",
    summary="Probe live Prometheus and Loki connectivity",
    description="Performs active probe queries against configured or specified telemetry endpoints.",
)
def telemetry_check(payload: Optional[TelemetryCheckRequest] = None) -> dict[str, Any]:
    prom_url = payload.prom_url if payload and payload.prom_url else resolve_prometheus_url()
    loki_url = payload.loki_url if payload and payload.loki_url else resolve_loki_url()

    results = check_live_stack(prom_url=prom_url, loki_url=loki_url)
    prom_msg = results.get("prometheus", "")
    loki_msg = results.get("loki", "")

    return {
        "prometheus": {
            "url": prom_url,
            "connected": not prom_msg.startswith("FAIL:"),
            "message": prom_msg,
        },
        "loki": {
            "url": loki_url,
            "connected": not loki_msg.startswith("FAIL:"),
            "message": loki_msg,
        },
    }


@router.post(
    "/telemetry/analyze",
    summary="Trigger telemetry anomaly analysis and AI scenario generation (mutating)",
    description=(
        "Collects telemetry, runs anomaly detection/clustering, and passes findings "
        "to the AI advisor. Stages generated experiments in pending HITL queue. "
        "Returns 409 Conflict if unapproved scenarios exist unless force=true is passed."
    ),
)
def telemetry_analyze(
    payload: TelemetryAnalyzeRequest,
    force: bool = Query(False, description="Force replacement of unapproved pending scenarios"),
    orch: OrchestratorDep = None,
    lock: MutatingLockDep = None,
    operator: OperatorDep = None,
) -> dict[str, Any]:
    with lock:
        # Symmetrical 409 Guard: Do not silently overwrite unapproved pending queue
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

        prom_url = resolve_prometheus_url() or ""
        loki_url = resolve_loki_url() or ""

        req = AnalysisRequest(
            source=payload.source,
            prom_url=prom_url,
            loki_url=loki_url,
            lookback_hours=payload.lookback_hours,
            export_path=payload.export_path,
            clustering_mode=payload.clustering_mode,
            n_clusters=payload.n_clusters,
            llm_provider=payload.llm_provider or "ollama",
            llm_model=payload.llm_model,
            skip_gatekeeper=payload.skip_gatekeeper,
            generate_scenarios=True,
        )

        try:
            result = run_gui_analysis_pipeline(req)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Telemetry analysis pipeline failed: {exc}",
            )

        if result.report and result.report.generated_experiments:
            orch.run_ai_experiment(result.report)

        return {
            "analyzed": True,
            "metric_series": result.metric_series,
            "log_streams": result.log_streams,
            "anomalies_found": len(result.clusters),
            "generated_experiments_count": len(result.report.generated_experiments) if result.report else 0,
            "replaced_unapproved_count": dropped_count,
        }
