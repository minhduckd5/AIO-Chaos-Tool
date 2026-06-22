"""
Advisor report persistence (P4 — Pipeline CLI Integration).

Writes the latest ``AdvisorReport`` to disk so stateless CLI invocations
(``incidents``, ``promote``) can reference a prior ``generate`` / ``analyze``
run without waiting for P5 SQLite.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from chaosgen.config.paths import CONFIG_DIR, ensure_config_dir
from chaosgen.schemas.scenarios import AdvisorReport

logger = logging.getLogger(__name__)

LAST_REPORT_FILE = CONFIG_DIR / "last_report.json"


def default_report_path() -> Path:
    """Return the default on-disk path for the most recent advisor report."""
    return LAST_REPORT_FILE


def save_report(report: AdvisorReport, path: Optional[Path | str] = None) -> Path:
    """Serialize ``report`` to JSON and return the path written."""
    target = Path(path) if path is not None else LAST_REPORT_FILE
    ensure_config_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Advisor report saved to %s", target)
    return target


def load_report(path: Optional[Path | str] = None) -> AdvisorReport:
    """Load an ``AdvisorReport`` from JSON. Raises ``FileNotFoundError`` if missing."""
    target = Path(path) if path is not None else LAST_REPORT_FILE
    if not target.exists():
        raise FileNotFoundError(f"No advisor report at {target}")
    data = json.loads(target.read_text(encoding="utf-8"))
    return AdvisorReport.model_validate(data)
