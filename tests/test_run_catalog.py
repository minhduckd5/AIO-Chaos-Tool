"""Run catalog + telemetry window resolution tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chaosgen.evaluation.ctk_verdict import build_verdict_from_ctk_run
from chaosgen.evaluation.run_catalog import (
    RunCatalogError,
    estimate_duration_from_experiment_json,
    record_from_journal,
    record_from_verdict,
    resolve_run_window,
    scan_journal_directory,
)
from chaosgen.evaluation.run_telemetry import cache_run_telemetry, load_cached_run_telemetry
from chaosgen.schemas.experiment_run import WindowResolutionSource
from chaosgen.schemas.scenarios import ExperimentVerdict
from chaosgen.schemas.telemetry import TelemetryDataset

FIXTURES = Path(__file__).parent / "fixtures" / "ctk_journal"


def test_resolve_window_from_journal_with_padding():
    from chaosgen.evaluation.ctk_journal import parse_ctk_journal

    path = FIXTURES / "completed_no_deviation.json"
    summary = parse_ctk_journal(path)
    start, end, source, warnings = resolve_run_window(
        summary, path, padding_seconds=60
    )
    assert source == WindowResolutionSource.JOURNAL
    assert warnings == []
    assert start == datetime(2026, 8, 28, 9, 59, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 28, 10, 1, 30, tzinfo=timezone.utc)


def test_record_from_journal():
    path = FIXTURES / "completed_no_deviation.json"
    record = record_from_journal(path, padding_seconds=60)
    assert record.experiment_name == "multi-kill"
    assert record.ctk_status == "completed"
    assert record.window_source == WindowResolutionSource.JOURNAL
    assert record.journal_path.endswith("completed_no_deviation.json")


def test_mtime_fallback_when_start_end_missing(tmp_path):
    journal = tmp_path / "no_times.json"
    journal.write_text(
        json.dumps(
            {
                "status": "completed",
                "experiment": {"title": "fallback-run"},
                "run": [],
            }
        ),
        encoding="utf-8",
    )
    from chaosgen.evaluation.ctk_journal import parse_ctk_journal

    summary = parse_ctk_journal(journal)
    start, end, source, warnings = resolve_run_window(
        summary, journal, padding_seconds=0, default_duration_seconds=120
    )
    assert source == WindowResolutionSource.MTIME_ESTIMATE
    assert len(warnings) == 1
    assert end > start


def test_missing_journal_raises():
    with pytest.raises(RunCatalogError):
        record_from_journal(Path("/nonexistent/journal.json"), padding_seconds=60)


def test_record_from_verdict(tmp_path):
    journal = FIXTURES / "completed_no_deviation.json"
    verdict_path = tmp_path / "verdict.json"
    report = build_verdict_from_ctk_run(
        {
            "success": True,
            "journal_path": str(journal),
            "journal_status": "completed",
            "deviated": False,
        },
        experiment_name="multi-kill",
    )
    verdict_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    record = record_from_verdict(verdict_path, padding_seconds=30)
    assert record.verdict == ExperimentVerdict.PASS
    assert record.experiment_name == "multi-kill"
    assert record.verdict_path == str(verdict_path.resolve())


def test_scan_journal_directory():
    records = scan_journal_directory(FIXTURES, padding_seconds=0)
    assert len(records) >= 4


def test_estimate_duration_from_experiment_json(tmp_path):
    exp = tmp_path / "experiment.json"
    exp.write_text(
        json.dumps(
            {
                "title": "t",
                "description": "d",
                "method": [
                    {"type": "action", "name": "a1", "pauses": {"after": "10s"}},
                    {"type": "action", "name": "a2", "pauses": {"after": "20s"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    duration = estimate_duration_from_experiment_json(exp)
    assert duration is not None
    assert duration >= 60 + 30 + 10 + 30 + 20


class _StubCollector:
    def collect_range(self, start, end, step="60s"):
        return TelemetryDataset(
            metrics=[],
            logs=[],
            collection_start=start,
            collection_end=end,
        )


def test_cache_and_load_run_telemetry(tmp_path):
    path = FIXTURES / "completed_no_deviation.json"
    record = record_from_journal(path, padding_seconds=0)
    updated = cache_run_telemetry(
        record,
        _StubCollector(),
        output_dir=tmp_path,
        step="60s",
    )
    assert updated.telemetry_cache_path
    loaded_record, dataset = load_cached_run_telemetry(updated.telemetry_cache_path)
    assert loaded_record.run_id == record.run_id
    assert dataset.collection_start == record.window_start
