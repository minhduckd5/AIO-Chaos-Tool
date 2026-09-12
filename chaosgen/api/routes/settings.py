"""Settings management routes (GET masked, PUT with active execution 409 guard)."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, HTTPException

from chaosgen.api.deps import MutatingLockDep, OperatorDep, OrchestratorDep
from chaosgen.config.settings import ChaosGenSettings, load_settings, save_settings

router = APIRouter(prefix="/v1", tags=["settings"])

_SENSITIVE_KEY_SUBSTRINGS = ("key", "token", "secret", "password", "credential")
ACTIVE_EXPERIMENT_STATES = {"steady_state_check", "injecting", "verifying", "rollback"}


def _mask_sensitive_values(data: Any) -> Any:
    """Recursively mask sensitive keys in configuration dictionaries."""
    if isinstance(data, dict):
        masked = {}
        for k, v in data.items():
            if any(s in k.lower() for s in _SENSITIVE_KEY_SUBSTRINGS) and isinstance(v, str) and len(v) > 0:
                masked[k] = "******" if len(v) <= 8 else f"{v[:2]}******{v[-2:]}"
            else:
                masked[k] = _mask_sensitive_values(v)
        return masked
    if isinstance(data, list):
        return [_mask_sensitive_values(item) for item in data]
    return data


@router.get(
    "/settings",
    summary="Get current configuration (masked)",
    description="Returns active settings with sensitive credentials and tokens masked.",
)
def get_settings() -> dict[str, Any]:
    settings = load_settings()
    data = settings.model_dump(mode="json")
    masked_data = _mask_sensitive_values(data)
    return {
        "settings": masked_data,
    }


@router.put(
    "/settings",
    summary="Update configuration (mutating)",
    description=(
        "Persists updated settings to disk and reloads orchestrator config. "
        "Returns 409 Conflict if an experiment is actively executing."
    ),
)
def update_settings(
    new_settings: dict[str, Any],
    orch: OrchestratorDep = None,
    lock: MutatingLockDep = None,
    operator: OperatorDep = None,
) -> dict[str, Any]:
    # 409 Guard: Cannot update settings while experiment is active
    if orch.state in ACTIVE_EXPERIMENT_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot update settings while orchestrator is in active state '{orch.state}'. "
                "Wait for experiment to complete or HALT before saving configuration."
            ),
        )

    # Validate against ChaosGenSettings schema
    try:
        # Merge with existing settings so partial updates don't wipe unmentioned blocks
        current = load_settings().model_dump(mode="json")
        merged = copy.deepcopy(current)
        merged.update(new_settings)
        parsed = ChaosGenSettings.model_validate(merged)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid settings payload: {exc}",
        )

    with lock:
        # --- START MODIFICATION ---
        # Config mutation path: use api_config, never api_hitl (reserved for AI HITL approve gate)
        orch.set_audit_context(actor=operator, path_used="api_config")
        # --- END MODIFICATION ---
        save_settings(parsed)
        if hasattr(orch, "reload_cg_settings"):
            orch.reload_cg_settings()

        return {
            "saved": True,
            "settings": _mask_sensitive_values(parsed.model_dump(mode="json")),
        }
