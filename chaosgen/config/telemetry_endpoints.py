"""
Resolve Prometheus / Loki base URLs for live ingestion.

Priority: settings.yaml observability hints → registry-vm defaults (with warning).
"""

from __future__ import annotations

import logging

from chaosgen.config.settings import ChaosGenSettings, ChaosGenSettings as Settings
from chaosgen.schemas.discovery import ObservabilityProfile, ObservabilityTool

logger = logging.getLogger(__name__)

# MODIFIED: P8 — demote silent hardcoded IP; warn when used as fallback
DEFAULT_REGISTRY_IP = "10.50.1.220"
DEFAULT_PROMETHEUS_URL = f"http://{DEFAULT_REGISTRY_IP}:9090"
DEFAULT_LOKI_URL = f"http://{DEFAULT_REGISTRY_IP}:3100"


def resolve_prometheus_url(settings: ChaosGenSettings | None = None) -> str:
    for hint in (settings.hints.observability if settings else []):
        if hint.tool == ObservabilityTool.PROMETHEUS:
            return hint.url.rstrip("/")
    logger.warning(
        "No prometheus URL in settings.hints.observability — falling back to "
        "DEFAULT_PROMETHEUS_URL (%s). Run `chaosgen config init` or set hints.",
        DEFAULT_PROMETHEUS_URL,
    )
    return DEFAULT_PROMETHEUS_URL


def resolve_loki_url(settings: ChaosGenSettings | None = None) -> str:
    for hint in (settings.hints.observability if settings else []):
        if hint.tool == ObservabilityTool.LOKI:
            return hint.url.rstrip("/")
    logger.warning(
        "No loki URL in settings.hints.observability — falling back to "
        "DEFAULT_LOKI_URL (%s). Run `chaosgen config init` or set hints.",
        DEFAULT_LOKI_URL,
    )
    return DEFAULT_LOKI_URL


def build_observability_profile(settings: Settings | None = None) -> ObservabilityProfile:
    """Observability profile for microservices-focused pipeline (live endpoints)."""
    prom = resolve_prometheus_url(settings)
    loki = resolve_loki_url(settings)
    detected = [ObservabilityTool.PROMETHEUS, ObservabilityTool.LOKI]
    missing = [t for t in ObservabilityTool if t not in detected]
    return ObservabilityProfile(
        has_metrics=True,
        metrics_endpoint=prom,
        has_logs=True,
        logs_endpoint=loki,
        has_traces=False,
        detected=detected,
        missing=missing,
    )
