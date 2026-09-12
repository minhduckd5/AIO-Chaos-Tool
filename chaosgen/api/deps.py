"""FastAPI dependencies: process-local orchestrator, operator header, mutating lock."""

from __future__ import annotations

import threading
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Request

from chaosgen.orchestrator import ChaosOrchestrator

OPERATOR_HEADER = "X-Operator-Name"


def get_orchestrator(request: Request) -> ChaosOrchestrator:
    orch = getattr(request.app.state, "orchestrator", None)
    if orch is None:
        raise HTTPException(status_code=503, detail="orchestrator not initialized")
    return orch


def get_mutating_lock(request: Request) -> threading.Lock:
    lock = getattr(request.app.state, "mutating_lock", None)
    if lock is None:
        raise HTTPException(status_code=503, detail="mutating lock not initialized")
    return lock


def require_operator_name(
    x_operator_name: Annotated[Optional[str], Header(alias=OPERATOR_HEADER)] = None,
) -> str:
    actor = (x_operator_name or "").strip()
    if not actor:
        raise HTTPException(
            status_code=401,
            detail=f"missing or blank {OPERATOR_HEADER} header",
        )
    return actor


OrchestratorDep = Annotated[ChaosOrchestrator, Depends(get_orchestrator)]
MutatingLockDep = Annotated[threading.Lock, Depends(get_mutating_lock)]
OperatorDep = Annotated[str, Depends(require_operator_name)]
