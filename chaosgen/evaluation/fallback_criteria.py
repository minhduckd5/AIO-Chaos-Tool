"""
Resolve default acceptance criteria for CTK Evaluation when none is attached.

Production-scale reference: examples/demo-expectation-criteria.yaml (threshold>=2).
Thesis boutique lab: examples/lab-boutique-expectation-criteria.yaml (threshold>=1).

Priority:
1. settings.evaluation.fallback_criteria_path (explicit)
2. Prometheus/Loki hint URL matches lab_url_markers → lab boutique file
3. Else → production demo file
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from chaosgen.config.settings import ChaosGenSettings, load_settings
from chaosgen.config.telemetry_endpoints import resolve_prometheus_url

logger = logging.getLogger(__name__)

LAB_BOUTIQUE_CRITERIA_REL = "examples/lab-boutique-expectation-criteria.yaml"
PRODUCTION_DEMO_CRITERIA_REL = "examples/demo-expectation-criteria.yaml"


def _project_root() -> Path:
    # chaosgen/evaluation/fallback_criteria.py → repo root
    return Path(__file__).resolve().parents[2]


def _resolve_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (_project_root() / p).resolve()


def _observability_urls(settings: ChaosGenSettings) -> list[str]:
    urls: list[str] = []
    for hint in settings.hints.observability or []:
        if hint.url:
            urls.append(str(hint.url))
    try:
        urls.append(resolve_prometheus_url(settings))
    except Exception:
        pass
    return urls


def _looks_like_lab(settings: ChaosGenSettings) -> bool:
    markers = [
        m.strip().lower()
        for m in (settings.evaluation.lab_url_markers or [])
        if m and str(m).strip()
    ]
    if not markers:
        return False
    blob = " ".join(_observability_urls(settings)).lower()
    return any(m in blob for m in markers)


def resolve_fallback_criteria_path(
    settings: ChaosGenSettings | None = None,
) -> Path | None:
    """Return criteria file path for CTK runs with no per-experiment criteria."""
    settings = settings if settings is not None else load_settings()
    explicit = (settings.evaluation.fallback_criteria_path or "").strip()
    if explicit:
        path = _resolve_path(explicit)
        if path.is_file():
            return path
        logger.warning(
            "evaluation.fallback_criteria_path=%s not found — falling back to auto",
            explicit,
        )

    rel = (
        LAB_BOUTIQUE_CRITERIA_REL
        if _looks_like_lab(settings)
        else PRODUCTION_DEMO_CRITERIA_REL
    )
    path = _project_root() / rel
    if path.is_file():
        return path.resolve()
    logger.warning("Fallback criteria file missing: %s", path)
    return None


def load_fallback_acceptance_criteria(
    settings: ChaosGenSettings | None = None,
) -> dict[str, Any] | None:
    """Load YAML/JSON fallback criteria dict, or None if unavailable."""
    path = resolve_fallback_criteria_path(settings)
    if path is None:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            logger.warning("Fallback criteria at %s is not a mapping", path)
            return None
        logger.info(
            "CTK evaluation fallback criteria: %s (lab=%s)",
            path,
            _looks_like_lab(settings if settings is not None else load_settings()),
        )
        return data
    except Exception as exc:
        logger.warning("Failed to load fallback criteria %s: %s", path, exc)
        return None
