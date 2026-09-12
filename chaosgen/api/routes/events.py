"""Server-Sent Events (SSE) read-only event stream route."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from chaosgen.api.deps import OrchestratorDep

router = APIRouter(prefix="/v1", tags=["events"])


@router.get(
    "/events/stream",
    summary="Read-only Server-Sent Events (SSE) stream",
    description="Streams orchestrator state transitions, active runs, and heartbeat events.",
)
async def event_stream(orch: OrchestratorDep = None) -> StreamingResponse:
    async def sse_generator() -> AsyncGenerator[str, None]:
        last_state = None
        last_outcome = None
        last_run_id = None

        # Initial handshake
        initial = {
            "type": "connected",
            "state": getattr(orch, "state", "idle") if orch else "unknown",
            "run_id": getattr(orch, "_run_id", None) if orch else None,
        }
        yield f"data: {json.dumps(initial)}\n\n"

        try:
            while True:
                current_state = getattr(orch, "state", "idle") if orch else "unknown"
                current_outcome = getattr(orch, "last_outcome", None) if orch else None
                current_run_id = getattr(orch, "_run_id", None) if orch else None

                # Yield on change or periodically
                if (
                    current_state != last_state
                    or current_outcome != last_outcome
                    or current_run_id != last_run_id
                ):
                    last_state = current_state
                    last_outcome = current_outcome
                    last_run_id = current_run_id

                    payload = {
                        "type": "state_change",
                        "state": current_state,
                        "run_id": current_run_id,
                        "outcome": current_outcome,
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                else:
                    # Keepalive heartbeat
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"

                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            # Client disconnected
            return

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
