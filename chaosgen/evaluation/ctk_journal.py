"""
Parse Chaos Toolkit experiment journals into structured summaries.

Journal files are written by ``chaos run --journal-path``. Schema tolerates
extra fields across chaostoolkit versions.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class CtkRunActivitySummary(BaseModel):
    """One executed activity from journal ``run[]``."""

    model_config = ConfigDict(extra="allow")

    name: str = ""
    activity_type: str = ""
    status: str = ""
    message: str = ""


class CtkRunSummary(BaseModel):
    """Normalized view of a CTK journal for verdict building."""

    model_config = ConfigDict(extra="allow")

    journal_path: Optional[str] = None
    status: Optional[str] = None
    deviated: Optional[bool] = None
    start: Optional[str] = None
    end: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    activities: List[CtkRunActivitySummary] = Field(default_factory=list)
    rollback_statuses: List[str] = Field(default_factory=list)
    parse_errors: List[str] = Field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None


def _activity_name(entry: Dict[str, Any]) -> str:
    activity = entry.get("activity") or {}
    if isinstance(activity, dict):
        return str(activity.get("name") or entry.get("name") or "activity")
    return str(entry.get("name") or "activity")


def _activity_type(entry: Dict[str, Any]) -> str:
    activity = entry.get("activity") or {}
    if isinstance(activity, dict):
        return str(activity.get("type") or "")
    return str(entry.get("type") or "")


def _normalize_summary(data: Dict[str, Any], journal_path: Optional[str]) -> CtkRunSummary:
    experiment = data.get("experiment") or {}
    title = experiment.get("title") if isinstance(experiment, dict) else None
    description = experiment.get("description") if isinstance(experiment, dict) else None

    activities: List[CtkRunActivitySummary] = []
    for entry in data.get("run") or []:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").lower()
        err = entry.get("exception") or entry.get("error") or entry.get("output")
        msg = ""
        if isinstance(err, str):
            msg = err[:200]
        elif err is not None:
            msg = str(err)[:200]
        activities.append(
            CtkRunActivitySummary(
                name=_activity_name(entry),
                activity_type=_activity_type(entry),
                status=status,
                message=msg,
            )
        )

    rollback_statuses: List[str] = []
    rollbacks = data.get("rollbacks")
    if isinstance(rollbacks, list):
        for rb in rollbacks:
            if isinstance(rb, dict):
                rollback_statuses.append(str(rb.get("status") or "").lower())
            else:
                rollback_statuses.append(str(rb).lower())

    return CtkRunSummary(
        journal_path=journal_path,
        status=str(data.get("status") or "").lower() or None,
        deviated=data.get("deviated") if "deviated" in data else None,
        start=data.get("start"),
        end=data.get("end"),
        title=str(title or "") or None,
        description=str(description or "") or None,
        activities=activities,
        rollback_statuses=rollback_statuses,
        raw=data,
    )


def parse_ctk_journal(
    source: Union[str, Path, Dict[str, Any]],
    *,
    retries: int = 3,
    retry_delay_s: float = 0.2,
) -> CtkRunSummary:
    """
    Load and normalize a CTK journal.

    ``source`` may be a file path or already-parsed dict (from module result).
    """
    if isinstance(source, dict):
        return _normalize_summary(source, None)

    path = Path(source)
    last_err: Optional[str] = None
    for attempt in range(max(1, retries)):
        if not path.is_file():
            last_err = f"journal file not found: {path}"
            if attempt < retries - 1:
                time.sleep(retry_delay_s)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            data = json.loads(text)
            if not isinstance(data, dict):
                return CtkRunSummary(
                    journal_path=str(path),
                    parse_errors=["journal root is not a JSON object"],
                )
            summary = _normalize_summary(data, str(path))
            return summary
        except json.JSONDecodeError as exc:
            last_err = f"invalid JSON: {exc}"
        except OSError as exc:
            last_err = str(exc)
        if attempt < retries - 1:
            time.sleep(retry_delay_s)

    return CtkRunSummary(
        journal_path=str(path),
        parse_errors=[last_err or "journal unreadable"],
    )
