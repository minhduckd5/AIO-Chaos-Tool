"""
Build ExpectationVerdictReport from CTK journal + optional Prometheus criteria.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from chaosgen.evaluation.ctk_journal import CtkRunSummary, parse_ctk_journal
from chaosgen.evaluation.expectation_verdict import ExpectationCheckResult, ExpectationVerdictReport
from chaosgen.schemas.scenarios import ExperimentVerdict

logger = logging.getLogger(__name__)

_VERDICT_RANK = {
    ExperimentVerdict.FAIL: 3,
    ExperimentVerdict.PARTIAL: 2,
    ExperimentVerdict.PASS: 1,
}


def _worst(a: ExperimentVerdict, b: ExperimentVerdict) -> ExperimentVerdict:
    return a if _VERDICT_RANK[a] >= _VERDICT_RANK[b] else b


def _failed_activity_status(status: str) -> bool:
    s = (status or "").lower()
    return s in ("failed", "error", "timeout", "deviated")


def _journal_verdict(
    summary: CtkRunSummary,
    module_result: Dict[str, Any],
) -> tuple[ExperimentVerdict, list[ExpectationCheckResult], list[str]]:
    checks: list[ExpectationCheckResult] = []
    rationale_parts: list[str] = []

    if module_result.get("dry_run"):
        ok = bool(module_result.get("success"))
        checks.append(
            ExpectationCheckResult(
                id="ctk_validate",
                check_type="ctk",
                passed=ok,
                message="chaos validate (dry-run)" if ok else module_result.get("error") or "validate failed",
            )
        )
        verdict = ExperimentVerdict.PASS if ok else ExperimentVerdict.FAIL
        rationale_parts.append(
            "Dry-run schema validation passed." if ok else "Dry-run validation failed."
        )
        return verdict, checks, rationale_parts

    if summary.parse_errors:
        checks.append(
            ExpectationCheckResult(
                id="ctk_journal_parse",
                check_type="ctk",
                passed=False,
                message="; ".join(summary.parse_errors),
            )
        )
        return (
            ExperimentVerdict.PARTIAL,
            checks,
            ["Journal could not be parsed; subprocess outcome may still be informative."],
        )

    if module_result.get("aborted"):
        checks.append(
            ExpectationCheckResult(
                id="ctk_aborted",
                check_type="ctk",
                passed=False,
                message="experiment aborted by operator (HALT)",
            )
        )
        rationale_parts.append("Run was aborted before CTK finished.")

    status = (summary.status or module_result.get("journal_status") or "").lower()
    completed = status == "completed"
    checks.append(
        ExpectationCheckResult(
            id="ctk_status",
            check_type="ctk",
            passed=completed and not module_result.get("aborted"),
            message=f"journal status={status or 'unknown'}",
        )
    )
    if not completed and not module_result.get("aborted"):
        rationale_parts.append(f"CTK did not complete (status={status or 'unknown'}).")

    deviated = summary.deviated
    if deviated is None:
        deviated = module_result.get("deviated")
    if deviated is not None:
        checks.append(
            ExpectationCheckResult(
                id="ctk_steady_state",
                check_type="ctk",
                passed=not bool(deviated),
                message="steady-state hypothesis deviated" if deviated else "no steady-state deviation",
            )
        )
        if deviated:
            rationale_parts.append("Steady-state hypothesis reported deviation.")

    failed_activities = [
        a for a in summary.activities if _failed_activity_status(a.status)
    ]
    for act in failed_activities:
        checks.append(
            ExpectationCheckResult(
                id=f"ctk_activity_{act.name}",
                check_type="ctk",
                passed=False,
                message=f"{act.name}: {act.status}" + (f" — {act.message}" if act.message else ""),
            )
        )
    if failed_activities:
        rationale_parts.append(
            f"{len(failed_activities)} CTK activity(ies) failed."
        )

    if summary.activities and not failed_activities:
        checks.append(
            ExpectationCheckResult(
                id="ctk_activities",
                check_type="ctk",
                passed=True,
                message=f"all {len(summary.activities)} method activities succeeded",
            )
        )

    failed_rollbacks = [
        s for s in summary.rollback_statuses if _failed_activity_status(s)
    ]
    if summary.rollback_statuses:
        checks.append(
            ExpectationCheckResult(
                id="ctk_rollbacks",
                check_type="ctk",
                passed=not failed_rollbacks,
                required=False,
                message=(
                    "rollbacks completed"
                    if not failed_rollbacks
                    else f"rollback issues: {failed_rollbacks}"
                ),
            )
        )
        if failed_rollbacks:
            rationale_parts.append("Some rollback steps did not succeed.")

    if not checks:
        checks.append(
            ExpectationCheckResult(
                id="ctk_completed",
                check_type="ctk",
                passed=bool(module_result.get("success")),
                message="subprocess outcome only (empty journal run log)",
            )
        )

    verdict = ExperimentVerdict.PASS
    for check in checks:
        if not check.passed and check.required:
            verdict = _worst(verdict, ExperimentVerdict.FAIL)
        elif not check.passed and not check.required:
            verdict = _worst(verdict, ExperimentVerdict.PARTIAL)

    if verdict == ExperimentVerdict.PASS and not rationale_parts:
        rationale_parts.append("CTK run completed without required check failures.")

    return verdict, checks, rationale_parts


def build_verdict_from_ctk_run(
    module_result: Dict[str, Any],
    *,
    experiment_name: Optional[str] = None,
    claim: Optional[str] = None,
    description: Optional[str] = None,
    acceptance_criteria: Optional[Dict[str, Any]] = None,
    prometheus_url: Optional[str] = None,
    poll_telemetry: bool = True,
) -> ExpectationVerdictReport:
    """Compose operational verdict from CTK module result + journal file."""
    journal_path = module_result.get("journal_path")
    summary: CtkRunSummary
    if journal_path:
        summary = parse_ctk_journal(journal_path)
    elif module_result.get("journal"):
        summary = parse_ctk_journal(module_result["journal"])
    else:
        summary = CtkRunSummary(parse_errors=["no journal path in module result"])

    title = experiment_name or summary.title or module_result.get("ctk_title") or "CTK experiment"
    claim_text = claim or summary.title or title
    if description or summary.description:
        desc = (description or summary.description or "").strip()
        if desc and desc != claim_text:
            claim_text = f"{claim_text}: {desc}"

    journal_verdict, checks, rationale_parts = _journal_verdict(summary, module_result)
    verdict = journal_verdict

    if acceptance_criteria:
        try:
            from chaosgen.advisor.catalog_promoter import evaluate_acceptance_detailed

            prom_report = evaluate_acceptance_detailed(
                acceptance_criteria,
                experiment_name=title,
                poll=poll_telemetry,
                prometheus_url=prometheus_url,
            )
            checks.extend(prom_report.checks)
            verdict = _worst(verdict, prom_report.verdict)
            if prom_report.rationale:
                rationale_parts.append(f"Telemetry: {prom_report.rationale}")
        except Exception as exc:
            logger.warning("Prometheus expectation merge failed: %s", exc)
            checks.append(
                ExpectationCheckResult(
                    id="telemetry_merge",
                    check_type="prometheus",
                    passed=False,
                    required=False,
                    message=f"telemetry evaluation failed: {exc}",
                )
            )
            verdict = _worst(verdict, ExperimentVerdict.PARTIAL)

    rationale = " ".join(rationale_parts).strip() or "CTK evaluation complete."
    metadata: Dict[str, Any] = {
        "source": "ctk_journal",
        "journal_path": summary.journal_path or journal_path,
        "ctk_status": summary.status,
        "deviated": summary.deviated,
        "aborted": bool(module_result.get("aborted")),
        "dry_run": bool(module_result.get("dry_run")),
        "experiment_path": module_result.get("experiment_file") or module_result.get("experiment_path"),
    }

    return ExpectationVerdictReport(
        claim=claim_text,
        verdict=verdict,
        rationale=rationale,
        checks=checks,
        experiment_name=title,
        metadata=metadata,
    )


def merge_verdict_reports(
    primary: ExpectationVerdictReport,
    secondary: ExpectationVerdictReport,
) -> ExpectationVerdictReport:
    """Merge two reports; worst verdict wins."""
    verdict = _worst(primary.verdict, secondary.verdict)
    return ExpectationVerdictReport(
        claim=primary.claim,
        verdict=verdict,
        rationale=f"{primary.rationale} | {secondary.rationale}",
        checks=primary.checks + secondary.checks,
        experiment_name=primary.experiment_name or secondary.experiment_name,
        metadata={**(secondary.metadata or {}), **(primary.metadata or {})},
    )
