"""CTK journal parse + verdict builder tests."""

from __future__ import annotations

from pathlib import Path

from chaosgen.evaluation.ctk_journal import parse_ctk_journal
from chaosgen.evaluation.ctk_verdict import build_verdict_from_ctk_run
from chaosgen.schemas.scenarios import ExperimentVerdict

FIXTURES = Path(__file__).parent / "fixtures" / "ctk_journal"


def test_parse_completed_journal():
    summary = parse_ctk_journal(FIXTURES / "completed_no_deviation.json")
    assert summary.status == "completed"
    assert summary.deviated is False
    assert len(summary.activities) == 2
    assert summary.title == "multi-kill"


def test_parse_missing_journal():
    summary = parse_ctk_journal(Path("/nonexistent/journal.json"), retries=1)
    assert summary.parse_errors


def test_verdict_pass_completed():
    path = FIXTURES / "completed_no_deviation.json"
    report = build_verdict_from_ctk_run(
        {
            "success": True,
            "journal_path": str(path),
            "journal_status": "completed",
            "deviated": False,
        },
        experiment_name="multi-kill",
    )
    assert report.verdict == ExperimentVerdict.PASS
    assert any(c.id == "ctk_status" and c.passed for c in report.checks)
    assert report.metadata.get("source") == "ctk_journal"


def test_verdict_fail_deviated():
    path = FIXTURES / "completed_deviated.json"
    report = build_verdict_from_ctk_run(
        {
            "success": True,
            "journal_path": str(path),
            "journal_status": "completed",
            "deviated": True,
        },
    )
    assert report.verdict == ExperimentVerdict.FAIL
    assert any(c.id == "ctk_steady_state" and not c.passed for c in report.checks)


def test_verdict_fail_aborted():
    path = FIXTURES / "aborted.json"
    report = build_verdict_from_ctk_run(
        {
            "success": False,
            "aborted": True,
            "journal_path": str(path),
            "journal_status": "aborted",
        },
    )
    assert report.verdict == ExperimentVerdict.FAIL
    assert any(c.id == "ctk_aborted" for c in report.checks)


def test_verdict_activity_failure():
    path = FIXTURES / "activity_failure.json"
    report = build_verdict_from_ctk_run(
        {
            "success": False,
            "journal_path": str(path),
            "journal_status": "completed",
        },
    )
    assert report.verdict == ExperimentVerdict.FAIL
    assert any("terminate-shipping" in c.id for c in report.checks)


def test_verdict_dry_run_validate():
    report = build_verdict_from_ctk_run(
        {"success": True, "dry_run": True, "message": "ok"},
        experiment_name="dry",
    )
    assert report.verdict == ExperimentVerdict.PASS
    assert any(c.id == "ctk_validate" for c in report.checks)
