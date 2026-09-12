"""Orphan resources storage and cleanup (Phase 2 GC)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

from chaosgen.storage.atomic_io import ExclusiveFileLock

logger = logging.getLogger(__name__)

DEFAULT_ORPHAN_PATH = Path(".chaosgen") / "orphan_resources.json"


def get_orphan_path(custom_path: Path | str | None = None) -> Path:
    if custom_path is not None:
        return Path(custom_path)
    return DEFAULT_ORPHAN_PATH


def read_orphans(custom_path: Path | str | None = None) -> List[dict[str, Any]]:
    """Read recorded orphan resources tolerating missing file or corrupt JSON."""
    path = get_orphan_path(custom_path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except Exception as exc:
        logger.warning("Failed to parse orphan resources at %s: %s", path, exc)
        return []


def sweep_orphans(
    custom_path: Path | str | None = None,
    older_than_seconds: int | None = None,
) -> List[dict[str, Any]]:
    """
    Safely read and clear the orphan resources file under an exclusive lock.

    Returns the list of cleared entries.
    """
    path = get_orphan_path(custom_path)
    if not path.is_file():
        return []

    lock_path = path.with_suffix(".lock")
    with ExclusiveFileLock(lock_path, timeout=5.0):
        try:
            content = path.read_text(encoding="utf-8")
            entries = json.loads(content) if content.strip() else []
            if not isinstance(entries, list):
                entries = []
        except Exception as exc:
            logger.warning("Error reading orphan file before sweep (%s): %s", path, exc)
            entries = []

        try:
            path.write_text("[]\n", encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to truncate orphan file %s: %s", path, exc)

        return entries
