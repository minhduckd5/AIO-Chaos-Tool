"""
Scenario Describer (P2 — Unknown Describe Loop).

Forces each gatekeeper-confirmed incident (verdict REAL or CHRONIC) through a
structured describe step before any chaos experiment is generated. Output is a
Pydantic-validated `UnknownScenarioDescription`.

Design:
- Guardrails live primarily in the schema (Field constraints + @field_validator),
  so instructor feeds a ValidationError back to the LLM for a retry. The prompt is
  supplementary, not the sole guard.
- Input contract: only `IncidentCandidate` with `passes_downstream is True` are
  describable. TRANSIENT / NOISE are a programming error and fail fast.
- When all retries are exhausted the describer returns a deterministic fallback
  description (knowledge_state=UNKNOWN, confidence=0.0, metadata.describe_fallback=True)
  instead of raising, so one failing incident never crashes a batch and the incident
  stays inside the Unknown -> Known knowledge loop.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from chaosgen.advisor.context_builder import ScenarioContext
from chaosgen.advisor.llm_advisor import LLMProvider, build_provider
from chaosgen.schemas.faults import FaultType
from chaosgen.schemas.incidents import IncidentCandidate
from chaosgen.schemas.scenarios import (
    AnomalySummary,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 2

_SYSTEM_PROMPT = """\
You are a Site Reliability Engineering incident analyst.
A gatekeeper has already confirmed the incident below is REAL and worth acting on.
Produce a STRUCTURED description of this still-unknown incident so engineers can
reproduce and reason about it BEFORE any chaos experiment is designed.

Respond with valid JSON matching this exact schema:
{{
  "title": "short specific title (>= 5 chars)",
  "root_cause_hypothesis": "concrete hypothesis grounded in the telemetry (>= 10 chars)",
  "repro_steps": ["at least 2 concrete, actionable steps"],
  "blast_radius_estimate": "must name the specific affected service/component",
  "suggested_fault_type": one of [{fault_types}],
  "confidence": float between 0.0 and 1.0,
  "source_incident_id": {incident_id},
  "knowledge_state": "described"
}}

Rules:
- repro_steps: minimum 2 steps, each specific and actionable.
- blast_radius_estimate MUST mention the affected service by name.
- Do NOT use vague hedges in root_cause_hypothesis such as: something wrong,
  maybe, could be, might be, possibly, not sure, unknown error.
- Base every claim on the provided telemetry; do not invent unrelated systems.
"""

_USER_PROMPT_TEMPLATE = """\
{context_block}
=== CONFIRMED INCIDENT (gatekeeper verdict: {verdict}) ===
Incident id: {incident_id}
Affected service: {service_target}
Gatekeeper rationale: {rationale}
Frequency: {frequency:.2f}/h
Severity: {severity:.2f}
Log correlated: {log_correlated}
Error pattern: {error_pattern}
Top deviating metrics: {features}
Gatekeeper metadata: {metadata}

Respond with a single JSON object. Do not include any text outside the JSON.
"""


def _dominant_feature_str(summary: Optional[AnomalySummary]) -> str:
    """Human-readable descriptor of the strongest signal for prompts/fallback."""
    if summary and summary.top_features:
        name, score = summary.top_features[0]
        return f"{name} (z={score:.2f})"
    if summary and summary.error_pattern:
        return summary.error_pattern
    return "unspecified telemetry deviation"


class ScenarioDescriber:
    """Describes gatekeeper-confirmed incidents via an LLM with schema enforcement."""

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        provider_name: str = "ollama",
        model: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._provider = provider or build_provider(provider_name, model=model)
        self.max_retries = max_retries

    # -- public API ---------------------------------------------------------

    def describe_batch(
        self,
        candidates: List[IncidentCandidate],
        summaries: List[AnomalySummary],
        context: Optional[ScenarioContext] = None,
    ) -> List[UnknownScenarioDescription]:
        """
        Describe each REAL/CHRONIC candidate. A single LLM failure yields a
        fallback description rather than aborting the batch.
        """
        summary_by_cluster = {s.source_cluster_id: s for s in summaries}
        results: List[UnknownScenarioDescription] = []
        for candidate in candidates:
            summary = summary_by_cluster.get(candidate.cluster_id)
            results.append(self.describe(candidate, summary, context))
        return results

    def describe(
        self,
        candidate: IncidentCandidate,
        summary: Optional[AnomalySummary],
        context: Optional[ScenarioContext] = None,
    ) -> UnknownScenarioDescription:
        """
        Describe a single confirmed incident.

        Raises ValueError if the candidate did not pass the gatekeeper (contract
        violation). LLM/validation failures are absorbed into a fallback object.
        """
        if not candidate.passes_downstream:
            raise ValueError(
                f"Describer rejects verdict {candidate.verdict.value}: "
                "only REAL/CHRONIC incidents are describable"
            )

        system_prompt = _SYSTEM_PROMPT.format(
            fault_types=", ".join(f'"{ft.value}"' for ft in FaultType),
            incident_id=candidate.cluster_id,
        )
        user_prompt = self._build_user_prompt(candidate, summary, context)

        last_error = "no attempt made"
        for attempt in range(1, self.max_retries + 1):
            try:
                result = self._provider.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=UnknownScenarioDescription,
                )
                self._validate_blast_radius(
                    result.blast_radius_estimate, candidate.service_target
                )
                result.source_incident_id = candidate.cluster_id
                result.knowledge_state = ScenarioKnowledgeState.DESCRIBED
                logger.info(
                    "describe success cluster=%d (attempt %d/%d)",
                    candidate.cluster_id, attempt, self.max_retries,
                )
                return result
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "describe attempt %d/%d failed cluster %d: %s",
                    attempt, self.max_retries, candidate.cluster_id, exc,
                )

        logger.error(
            "describe FALLBACK cluster %d after %d attempts: %s",
            candidate.cluster_id, self.max_retries, last_error,
        )
        return self._build_fallback_description(candidate, summary, last_error)

    # -- prompt + validation helpers ---------------------------------------

    def _build_user_prompt(
        self,
        candidate: IncidentCandidate,
        summary: Optional[AnomalySummary],
        context: Optional[ScenarioContext],
    ) -> str:
        context_block = f"{context.to_prompt_text()}\n\n" if context else ""
        return _USER_PROMPT_TEMPLATE.format(
            context_block=context_block,
            verdict=candidate.verdict.value,
            incident_id=candidate.cluster_id,
            service_target=candidate.service_target or "unknown service",
            rationale=candidate.rationale,
            frequency=candidate.frequency,
            severity=candidate.severity,
            log_correlated=candidate.log_correlated,
            error_pattern=(summary.error_pattern if summary else None) or "n/a",
            features=_dominant_feature_str(summary),
            metadata=candidate.metadata,
        )

    @staticmethod
    def _validate_blast_radius(estimate: str, service_target: Optional[str]) -> None:
        """Ensure the LLM named the affected service when one is known."""
        if service_target and service_target.lower() not in estimate.lower():
            raise ValueError(
                f"blast_radius_estimate must mention service '{service_target}'"
            )

    @staticmethod
    def _build_fallback_description(
        candidate: IncidentCandidate,
        summary: Optional[AnomalySummary],
        failure_reason: str,
    ) -> UnknownScenarioDescription:
        """Deterministic safe description used when the LLM cannot be trusted."""
        service = candidate.service_target or "unknown service"
        return UnknownScenarioDescription(
            title=f"Undescribed incident — cluster {candidate.cluster_id}",
            root_cause_hypothesis=(
                f"Telemetry anomaly on {service}: "
                f"{_dominant_feature_str(summary)}; "
                f"gatekeeper verdict={candidate.verdict.value}"
            ),
            repro_steps=[
                f"Inspect metrics and logs for {service}",
                "Correlate with deployment or traffic change in the lookback window",
            ],
            blast_radius_estimate=f"Primary impact: {service}",
            suggested_fault_type=FaultType.NETWORK_LATENCY,
            confidence=0.0,
            source_incident_id=candidate.cluster_id,
            knowledge_state=ScenarioKnowledgeState.UNKNOWN,
            metadata={
                "describe_fallback": True,
                "failure_reason": failure_reason,
            },
        )
