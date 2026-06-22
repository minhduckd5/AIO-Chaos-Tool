"""
Catalog promoter (P3 — Promote Known Catalog).

Closes the Unknown -> Known loop: a HITL operator promotes a described,
chaos-validated incident into the dynamic catalog. Promotion is gated — only a
genuinely described scenario (not a P2 fallback) with an operator sign-off and
syntactically valid acceptance criteria is written to disk.

Pipeline wiring (gatekeeper -> describer -> promote across CLI/GUI) belongs to
P4; this module is the promote primitive plus the verify helpers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from chaosgen.advisor.promoted_store import (
    PromotedCatalogRecord,
    PromotedStore,
    experiment_to_dict,
    get_default_store,
)
from chaosgen.advisor.scenario_catalog import CatalogEntry
from chaosgen.config.scope import FOCUSED_ARCHITECTURE
from chaosgen.schemas.faults import ChaosExperiment
from chaosgen.schemas.scenarios import (
    ExperimentVerdict,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)
from chaosgen.ucal.validation import SteadyStateValidator

logger = logging.getLogger(__name__)

_URL_PREFIXES = ("http://", "https://")


class PromoteError(ValueError):
    """Raised when a promotion is rejected by a guard (HITL, contract, criteria)."""


def validate_acceptance_criteria(criteria: Optional[Dict[str, Any]]) -> List[str]:
    """Pre-flight check acceptance criteria. Returns a list of error strings.

    Catches obvious garbage (LLM/operator typos) before it reaches disk and
    later blows up inside SteadyStateValidator at verify time. PromQL is only
    heuristically checked here; a full parse/dry-run is a P4/P5 enhancement.
    """
    errors: List[str] = []
    if not criteria:
        return errors

    http = criteria.get("http_health")
    if http is not None:
        if not isinstance(http, str) or not http.strip():
            errors.append("http_health must be a non-empty string")
        elif not http.startswith(_URL_PREFIXES):
            errors.append("http_health must start with http:// or https://")

    prometheus = criteria.get("prometheus")
    if prometheus is not None:
        if not isinstance(prometheus, dict):
            errors.append("prometheus must be an object")
        else:
            query = prometheus.get("query")
            if not isinstance(query, str) or not query.strip():
                errors.append("prometheus.query must be a non-empty string")
            elif query.count("{") != query.count("}"):
                errors.append("prometheus.query has unbalanced braces")
            url = prometheus.get("url")
            if url is not None and (
                not isinstance(url, str) or not url.startswith(_URL_PREFIXES)
            ):
                errors.append("prometheus.url must be a valid http(s) URL")

    for key in criteria:
        if key not in ("http_health", "prometheus"):
            logger.warning("Unknown acceptance_criteria key %r — kept but unvalidated", key)

    return errors


def evaluate_acceptance(
    criteria: Optional[Dict[str, Any]],
    validator: SteadyStateValidator | None = None,
) -> ExperimentVerdict:
    """Run acceptance criteria through the steady-state validator post-experiment.

    Returns PASS when there are no criteria or all checks hold, FAIL otherwise.
    PARTIAL is an operator decision (accepted residual risk), not inferred here.
    """
    if not criteria:
        return ExperimentVerdict.PASS
    validator = validator or SteadyStateValidator()
    return ExperimentVerdict.PASS if validator.validate(criteria) else ExperimentVerdict.FAIL


class CatalogPromoter:
    """HITL-gated promotion of described incidents into the dynamic catalog."""

    def __init__(
        self,
        store: PromotedStore | None = None,
        history_store: Any | None = None,
    ) -> None:
        self._store = store or get_default_store()
        self._history = history_store

    def promote(
        self,
        description: UnknownScenarioDescription,
        experiment: ChaosExperiment,
        approved_by: str,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        name: Optional[str] = None,
        description_id: Optional[str] = None,
        description_row_id: Optional[int] = None,
    ) -> CatalogEntry:
        """Validate guards, append to the store, and mark the scenario KNOWN.

        Raises PromoteError if any guard fails; nothing is written in that case.
        """
        self._check_guards(description, approved_by, acceptance_criteria, name)

        record = PromotedCatalogRecord(
            name=name or description.title,
            description=description.root_cause_hypothesis,
            architecture=FOCUSED_ARCHITECTURE,
            fault_type=description.suggested_fault_type,
            experiment_spec=experiment_to_dict(experiment),
            acceptance_criteria=acceptance_criteria,
            tags=tags or [],
            promoted_at=datetime.now(timezone.utc).isoformat(),
            approved_by=approved_by.strip(),
            source_incident_id=description.source_incident_id,
            description_id=description_id,
        )
        self._store.append(record)
        description.knowledge_state = ScenarioKnowledgeState.KNOWN
        if self._history is not None and description_row_id is not None:
            try:
                self._history.schedule_mark_promoted(
                    description_row_id,
                    record.name,
                    record.approved_by,
                )
            except Exception as exc:
                logger.warning("History mark_promoted failed (catalog saved): %s", exc)
        logger.info(
            "Promoted scenario %r (incident=%s) by %s",
            record.name, record.source_incident_id, record.approved_by,
        )
        return record.to_catalog_entry()

    def _check_guards(
        self,
        description: UnknownScenarioDescription,
        approved_by: str,
        acceptance_criteria: Optional[Dict[str, Any]],
        name: Optional[str],
    ) -> None:
        if description.knowledge_state != ScenarioKnowledgeState.DESCRIBED:
            raise PromoteError(
                f"can only promote DESCRIBED scenarios, got "
                f"{description.knowledge_state.value}"
            )
        if description.metadata.get("describe_fallback") is True:
            raise PromoteError("cannot promote a P2 fallback description (low confidence)")
        if not approved_by or not approved_by.strip():
            raise PromoteError("approved_by is required — HITL gate")

        criteria_errors = validate_acceptance_criteria(acceptance_criteria)
        if criteria_errors:
            raise PromoteError("invalid acceptance_criteria: " + "; ".join(criteria_errors))

        existing = self._store.load_records_safe()
        entry_name = name or description.title
        if any(record.name == entry_name for record in existing):
            raise PromoteError(f"duplicate scenario name: {entry_name!r}")
        if description.source_incident_id is not None and any(
            record.source_incident_id == description.source_incident_id
            for record in existing
        ):
            raise PromoteError(
                f"incident {description.source_incident_id} already promoted"
            )
