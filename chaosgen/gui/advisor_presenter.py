"""
Pure presentation helpers for the GUI Advisor view (P4.1).

These functions keep ``advisor_view.py`` thin and testable without a Qt event
loop: they map an ``AdvisorReport`` into plain row dicts and centralize the P3
promote guards that mirror the CLI ``promote`` command.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from chaosgen.schemas.incidents import IncidentVerdict
from chaosgen.schemas.scenarios import (
    AdvisorReport,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)

_VERDICT_DOWNSTREAM = {
    IncidentVerdict.REAL: "→ chaos",
    IncidentVerdict.CHRONIC: "→ chaos",
    IncidentVerdict.TRANSIENT: "monitor only",
    IncidentVerdict.NOISE: "filtered",
}


def _describe_status(report: AdvisorReport, cluster_id: int) -> str:
    """Describe outcome for a candidate row: described / fallback / —."""
    for desc in report.descriptions:
        if desc.source_incident_id == cluster_id:
            if desc.metadata.get("describe_fallback"):
                return "fallback"
            if desc.knowledge_state == ScenarioKnowledgeState.DESCRIBED:
                return "described"
            return desc.knowledge_state.value
    return "—"


def gatekeeper_rows(report: AdvisorReport) -> List[Dict[str, str]]:
    """Map gatekeeper candidates to display rows (NOISE is summarized, not listed)."""
    rows: List[Dict[str, str]] = []
    for cand in report.incident_candidates:
        rows.append(
            {
                "cluster": str(cand.cluster_id),
                "verdict": cand.verdict.value.upper(),
                "frequency": f"{cand.frequency:.1f}",
                "severity": f"{cand.severity:.2f}",
                "log": "yes" if cand.log_correlated else "no",
                "service": cand.service_target or "—",
                "describe": _describe_status(report, cand.cluster_id),
                "downstream": _VERDICT_DOWNSTREAM.get(cand.verdict, "—"),
                "rationale": cand.rationale,
            }
        )
    return rows


def description_rows(
    report: AdvisorReport,
    state_filter: Optional[str] = None,
) -> List[UnknownScenarioDescription]:
    """Return descriptions, optionally filtered by knowledge state (mirrors CLI)."""
    descriptions = list(report.descriptions)
    if state_filter and state_filter.lower() != "all":
        target = ScenarioKnowledgeState(state_filter.lower())
        descriptions = [d for d in descriptions if d.knowledge_state == target]
    return descriptions


def is_fallback(description: UnknownScenarioDescription) -> bool:
    """True when the description is a deterministic P2 fallback (not LLM-described)."""
    return bool(description.metadata.get("describe_fallback"))


def promote_blocked_reason(
    description: UnknownScenarioDescription,
    verdict: Optional["ExperimentVerdict"] = None,
) -> Optional[str]:
    """Return a human-readable block reason, or None if the description can promote.

    Mirrors the P3 promote guards enforced by ``CatalogPromoter``.
    When ``verdict`` is supplied, non-PASS blocks promotion (Option B).
    """
    if is_fallback(description):
        return "P2 fallback descriptions cannot be promoted (re-run describe)."
    if description.knowledge_state != ScenarioKnowledgeState.DESCRIBED:
        return (
            f"Only DESCRIBED incidents can be promoted "
            f"(state={description.knowledge_state.value})."
        )
    if verdict is not None:
        from chaosgen.schemas.scenarios import ExperimentVerdict

        if verdict != ExperimentVerdict.PASS:
            return (
                f"Only Expectation Verdict PASS can promote "
                f"(got {verdict.value})."
            )
    return None


def gatekeeper_summary(report: AdvisorReport) -> str:
    """One-line gatekeeper/describe summary for the Step 3 header."""
    described = sum(
        1
        for d in report.descriptions
        if d.knowledge_state == ScenarioKnowledgeState.DESCRIBED
        and not d.metadata.get("describe_fallback")
    )
    return (
        f"Gatekeeper: {len(report.incident_candidates)} candidates, "
        f"noise dropped: {report.filtered_noise_count}, "
        f"transient: {report.filtered_transient_count} | "
        f"Described: {described} | "
        f"Scenarios: {len(report.generated_experiments)}"
    )
