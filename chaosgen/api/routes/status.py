"""FSM + module status."""

from fastapi import APIRouter

from chaosgen.api.deps import OrchestratorDep

router = APIRouter(prefix="/v1", tags=["status"])


@router.get("/status")
def get_status(orch: OrchestratorDep) -> dict:
    return {
        "state": orch.state,
        "modules": orch.get_all_status(),
    }
