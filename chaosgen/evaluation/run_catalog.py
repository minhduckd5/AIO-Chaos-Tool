"""
Build ExperimentRunRecord catalog from CTK journals, verdict JSON, and history scans.

Windows use journal ``start``/``end`` when present, with configurable padding.
Fallback (mtime + estimated duration) is explicit — never silent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Union

from chaosgen.evaluation.ctk_journal import CtkRunSummary, parse_ctk_journal
from chaosgen.evaluation.expectation_verdict import ExpectationVerdictReport
from chaosgen.schemas.experiment_run import ExperimentRunRecord, WindowResolutionSource

logger = logging.getLogger(__name__)

DEFAULT_PADDING_SECONDS = 60
DEFAULT_ESTIMATED_DURATION_SECONDS = 300
DEFAULT_ACTION_SECONDS = 30
_PAUSE_AFTER_RE = re.compile(r"^(\d+(?:\.\d+)?)s$")


class RunCatalogError(ValueError):
    """Run record cannot be built from the given inputs."""


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _run_id_for_journal(journal_path: Path, summary: CtkRunSummary) -> str:
    digest = hashlib.sha256(str(journal_path.resolve()).encode("utf-8")).hexdigest()[:10]
    stem = journal_path.stem[:32] or "journal"
    if summary.start:
        stamp = summary.start.replace(":", "").replace("-", "").replace("T", "")[:14]
        return f"ctk-{stem}-{stamp}-{digest}"
    return f"ctk-{stem}-{digest}"


def _parse_pause_seconds(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = _PAUSE_AFTER_RE.match(value.strip())
        if m:
            return float(m.group(1))
    return 0.0


def estimate_duration_from_experiment_json(path: Optional[Path]) -> Optional[int]:
    """Heuristic blast-radius duration from CTK experiment ``method`` pauses."""
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Could not read experiment JSON for duration estimate: %s", exc)
        return None

    method = data.get("method") or []
    if not isinstance(method, list):
        return None

    total = 60.0
    for entry in method:
        if not isinstance(entry, dict):
            total += DEFAULT_ACTION_SECONDS
            continue
        pauses = entry.get("pauses") or {}
        if isinstance(pauses, dict):
            total += _parse_pause_seconds(pauses.get("after"))
        total += DEFAULT_ACTION_SECONDS

    return max(int(total), 60)


def resolve_run_window(
    summary: CtkRunSummary,
    journal_path: Path,
    *,
    padding_seconds: int = DEFAULT_PADDING_SECONDS,
    experiment_json_path: Optional[Path] = None,
    default_duration_seconds: int = DEFAULT_ESTIMATED_DURATION_SECONDS,
) -> tuple[datetime, datetime, WindowResolutionSource, List[str]]:
    """
    Derive UTC window for telemetry fetch.

    Raises ``RunCatalogError`` when no journal timestamps and journal file is missing.
    """
    warnings: List[str] = []
    estimated = estimate_duration_from_experiment_json(experiment_json_path)
    duration = estimated or default_duration_seconds

    if summary.start and summary.end:
        start = _parse_iso(summary.start)
        end = _parse_iso(summary.end)
        source = WindowResolutionSource.JOURNAL
    elif summary.start:
        start = _parse_iso(summary.start)
        end = start + timedelta(seconds=duration)
        warnings.append(
            f"journal missing end; estimated +{duration}s from experiment JSON or default"
        )
        source = WindowResolutionSource.JOURNAL_START_ESTIMATE
    elif journal_path.is_file():
        mtime = datetime.fromtimestamp(journal_path.stat().st_mtime, tz=timezone.utc)
        end = mtime
        start = end - timedelta(seconds=duration)
        warnings.append(
            f"journal missing start/end; window from file mtime −{duration}s"
        )
        source = WindowResolutionSource.MTIME_ESTIMATE
    else:
        raise RunCatalogError(
            f"cannot resolve window: journal unreadable and file missing ({journal_path})"
        )

    if end <= start:
        raise RunCatalogError(
            f"invalid window: end ({end.isoformat()}) must be after start ({start.isoformat()})"
        )

    pad = max(0, int(padding_seconds))
    return (
        start - timedelta(seconds=pad),
        end + timedelta(seconds=pad),
        source,
        warnings,
    )


def record_from_journal(
    journal_path: Union[str, Path],
    *,
    padding_seconds: int = DEFAULT_PADDING_SECONDS,
    verdict_report: Optional[ExpectationVerdictReport] = None,
    verdict_path: Optional[Path] = None,
    experiment_json_path: Optional[Union[str, Path]] = None,
) -> ExperimentRunRecord:
    """Build a run record from a CTK journal file (optional verdict enrichment)."""
    path = Path(journal_path)
    summary = parse_ctk_journal(path)
    if summary.parse_errors:
        raise RunCatalogError(
            "; ".join(summary.parse_errors) or f"journal unreadable: {path}"
        )

    exp_path: Optional[Path] = None
    if experiment_json_path:
        exp_path = Path(experiment_json_path)
    elif verdict_report and verdict_report.metadata.get("experiment_path"):
        exp_path = Path(str(verdict_report.metadata["experiment_path"]))

    window_start, window_end, source, warnings = resolve_run_window(
        summary,
        path,
        padding_seconds=padding_seconds,
        experiment_json_path=exp_path,
    )

    name = (
        verdict_report.experiment_name
        if verdict_report and verdict_report.experiment_name
        else summary.title
        or path.stem
    )

    verdict = verdict_report.verdict if verdict_report else None
    aborted = bool(verdict_report.metadata.get("aborted")) if verdict_report else False
    dry_run = bool(verdict_report.metadata.get("dry_run")) if verdict_report else False

    return ExperimentRunRecord(
        run_id=_run_id_for_journal(path, summary),
        experiment_name=str(name),
        window_start=window_start,
        window_end=window_end,
        window_padding_seconds=padding_seconds,
        window_source=source,
        verdict=verdict,
        ctk_status=summary.status,
        deviated=summary.deviated,
        aborted=aborted,
        dry_run=dry_run,
        journal_path=str(path.resolve()),
        experiment_json_path=str(exp_path.resolve()) if exp_path and exp_path.exists() else None,
        verdict_path=str(verdict_path.resolve()) if verdict_path else None,
        warnings=warnings,
    )


def record_from_verdict(
    verdict_path: Union[str, Path],
    *,
    padding_seconds: int = DEFAULT_PADDING_SECONDS,
) -> ExperimentRunRecord:
    """Build a run record from persisted ExpectationVerdictReport JSON."""
    from chaosgen.advisor.report_store import load_verdict_report

    path = Path(verdict_path)
    report = load_verdict_report(path)
    journal = report.metadata.get("journal_path")
    if not journal:
        raise RunCatalogError(f"verdict at {path} has no journal_path in metadata")

    return record_from_journal(
        journal,
        padding_seconds=padding_seconds,
        verdict_report=report,
        verdict_path=path,
        experiment_json_path=report.metadata.get("experiment_path"),
    )


def scan_journal_directory(
    directory: Union[str, Path],
    *,
    padding_seconds: int = DEFAULT_PADDING_SECONDS,
) -> List[ExperimentRunRecord]:
    """Load all ``*.json`` journals in a directory (skips failures with a log warning)."""
    root = Path(directory)
    if not root.is_dir():
        raise RunCatalogError(f"journal directory not found: {root}")

    records: List[ExperimentRunRecord] = []
    for path in sorted(root.glob("*.json")):
        try:
            records.append(
                record_from_journal(path, padding_seconds=padding_seconds)
            )
        except RunCatalogError as exc:
            logger.warning("Skipping journal %s: %s", path, exc)
    return records


def build_run_records(
    *,
    journal_paths: Optional[List[Union[str, Path]]] = None,
    journal_dir: Optional[Union[str, Path]] = None,
    verdict_path: Optional[Union[str, Path]] = None,
    padding_seconds: int = DEFAULT_PADDING_SECONDS,
) -> List[ExperimentRunRecord]:
    """Aggregate run records from explicit paths, a directory scan, or verdict JSON."""
    records: List[ExperimentRunRecord] = []
    seen_journal: set[str] = set()

    if verdict_path:
        rec = record_from_verdict(verdict_path, padding_seconds=padding_seconds)
        records.append(rec)
        if rec.journal_path:
            seen_journal.add(rec.journal_path)

    if journal_dir:
        for rec in scan_journal_directory(journal_dir, padding_seconds=padding_seconds):
            if rec.journal_path and rec.journal_path in seen_journal:
                continue
            records.append(rec)
            if rec.journal_path:
                seen_journal.add(rec.journal_path)

    for jp in journal_paths or []:
        path = Path(jp)
        key = str(path.resolve())
        if key in seen_journal:
            continue
        records.append(record_from_journal(path, padding_seconds=padding_seconds))
        seen_journal.add(key)

    return records
