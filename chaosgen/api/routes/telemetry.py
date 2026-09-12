"""Telemetry readiness — settings URLs only (no live scrape)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from chaosgen.config.settings import load_settings
from chaosgen.schemas.discovery import ObservabilityTool

router = APIRouter(prefix="/v1", tags=["telemetry"])


@router.get("/telemetry/ready")
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
