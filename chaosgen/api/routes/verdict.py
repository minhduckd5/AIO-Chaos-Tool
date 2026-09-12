"""Last expectation verdict report."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from chaosgen.advisor.report_store import load_verdict_report

router = APIRouter(prefix="/v1", tags=["verdict"])


@router.get("/verdict/last")
def verdict_last() -> dict[str, Any]:
    try:
        report = load_verdict_report()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return report.model_dump(mode="json")
