"""FastAPI application factory for the ChaosGen API sidecar."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI

from chaosgen.api.routes import audit, control, health, pending, status, telemetry, verdict
from chaosgen.orchestrator import ChaosOrchestrator


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    orch = getattr(app.state, "orchestrator", None)
    if orch is None:
        app.state.orchestrator = ChaosOrchestrator()
    if getattr(app.state, "mutating_lock", None) is None:
        app.state.mutating_lock = threading.Lock()
    yield


def create_app(
    *,
    orchestrator: Optional[ChaosOrchestrator] = None,
    mutating_lock: Optional[threading.Lock] = None,
) -> FastAPI:
    """
    Build the Phase 1 sidecar app.

    Dual-inject (GUI process + this API process) is an operational limit — not
    a cross-process mutex. Mutating routes serialize on ``mutating_lock``.
    """
    app = FastAPI(
        title="ChaosGen API",
        version="0.2.0",
        description=(
            "Additive HTTP surface for ChaosGen. "
            "Do not run alongside GUI inject (dual-inject operational limit). "
            "Mutating routes require X-Operator-Name and are serialized in-process."
        ),
        lifespan=_lifespan,
    )
    if orchestrator is not None:
        app.state.orchestrator = orchestrator
    if mutating_lock is not None:
        app.state.mutating_lock = mutating_lock

    app.include_router(health.router)
    app.include_router(status.router)
    app.include_router(pending.router)
    app.include_router(control.router)
    app.include_router(audit.router)
    app.include_router(verdict.router)
    app.include_router(telemetry.router)
    return app


# Uvicorn target: chaosgen.api.app:app
app = create_app()
