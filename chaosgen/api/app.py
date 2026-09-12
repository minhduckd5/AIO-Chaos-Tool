"""FastAPI application factory for the ChaosGen API sidecar."""

from __future__ import annotations

import os
from pathlib import Path
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from chaosgen.api.routes import (
    audit,
    catalog,
    control,
    events,
    experiments,
    health,
    pending,
    settings,
    status,
    telemetry,
    verdict,
)
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
    Build the ChaosGen API app.

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

    # Optional dev-only CORS
    env = os.getenv("CHAOSGEN_ENV", "").lower()
    cors_dev = os.getenv("CHAOSGEN_CORS_DEV", "0")
    if env in ("dev", "development") or cors_dev in ("1", "true"):
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:8765",
                "http://127.0.0.1:8765",
            ],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Register all API endpoints
    app.include_router(health.router)
    app.include_router(status.router)
    app.include_router(pending.router)
    app.include_router(control.router)
    app.include_router(audit.router)
    app.include_router(verdict.router)
    app.include_router(telemetry.router)
    app.include_router(catalog.router)
    app.include_router(experiments.router)
    app.include_router(settings.router)
    app.include_router(events.router)

    # Mount SPA static files if built
    web_dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    if web_dist.is_dir():
        index_html = web_dist / "index.html"
        app.mount("/assets", StaticFiles(directory=str(web_dist / "assets")), name="web_assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            candidate = web_dist / full_path
            if candidate.is_file() and full_path != "":
                from starlette.responses import FileResponse
                return FileResponse(candidate)
            from starlette.responses import FileResponse
            return FileResponse(index_html)

    return app
