"""Additive FastAPI sidecar for ChaosGen (Phase 1 explore)."""

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    from chaosgen.api.app import create_app as _create_app

    return _create_app(*args, **kwargs)
