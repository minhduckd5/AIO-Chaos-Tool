"""
LLM Advisor: Translates anomaly summaries into fault hypotheses
using a local LLM (Ollama) with structured JSON output enforcement
via the `instructor` library.

Prompt Architecture
-------------------
The LLM receives a compact AnomalySummary (~200-300 tokens per cluster)
rather than raw telemetry. The prompt is structured in three sections:

  1. SYSTEM: Role definition + output schema contract + valid enum values.
  2. CONTEXT: Rendered AnomalySummary blocks (one per cluster).
  3. INSTRUCTION: Mapping directive with confidence calibration guidance.

This design keeps total prompt size under 2K tokens for batches of up to
10 clusters, safely within a 4K context window on 8B-class models.
"""

import logging
from typing import List, Optional

from chaosgen.schemas.faults import FaultType
from chaosgen.schemas.scenarios import AnomalySummary, FaultHypothesis

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Chaos Engineering advisor for distributed systems.
Your task is to analyze telemetry anomaly summaries and recommend
specific fault injection scenarios to expose the underlying weakness.

You must respond with valid JSON matching this exact schema:
{
  "fault_type": one of [{fault_types}],
  "target_hint": "service or component name to target",
  "rationale": "1-2 sentence explanation of why this fault matches the anomaly",
  "confidence": float between 0.0 and 1.0,
  "source_cluster_id": integer matching the input cluster ID,
  "suggested_duration": "duration string e.g. 30s, 1m, 5m",
  "suggested_parameters": {"key": "value"} dict of fault parameters
}

Confidence calibration:
- 0.9-1.0: Clear causal link between anomaly and fault type
- 0.7-0.89: Strong correlation, likely match
- 0.5-0.69: Moderate correlation, worth investigating
- Below 0.5: Weak signal, low confidence

Valid fault types: {fault_types}
"""

USER_PROMPT_TEMPLATE = """\
Analyze the following anomaly cluster and recommend a chaos fault injection:

{anomaly_block}

Respond with a single JSON object matching the schema above. Do not include \
any text outside the JSON object.
"""

MAX_RETRIES = 2


class LLMAdvisor:
    """
    Maps AnomalySummary objects to FaultHypothesis objects using a local
    LLM via Ollama, with instructor-enforced Pydantic schema validation.
    """

    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.2,
    ):
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self._client = None
        self._instructor_client = None

    def _get_instructor_client(self):
        """Lazy-initialize the instructor-wrapped Ollama client."""
        if self._instructor_client is None:
            try:
                import instructor
                from ollama import Client as OllamaClient

                self._client = OllamaClient(host=self.base_url)
                self._instructor_client = instructor.from_ollama(
                    self._client,
                    mode=instructor.Mode.JSON,
                )
            except ImportError as e:
                logger.error(
                    "Required packages not installed. "
                    "Install with: pip install instructor ollama. Error: %s", e
                )
                raise
        return self._instructor_client

    def interpret_anomalies(
        self, summaries: List[AnomalySummary]
    ) -> List[FaultHypothesis]:
        """
        Process a batch of AnomalySummary objects through the LLM
        and return validated FaultHypothesis objects.
        """
        hypotheses: List[FaultHypothesis] = []

        for summary in summaries:
            hypothesis = self._interpret_single(summary)
            if hypothesis is not None:
                hypotheses.append(hypothesis)

        logger.info(
            "LLM produced %d hypotheses from %d summaries (%d skipped)",
            len(hypotheses),
            len(summaries),
            len(summaries) - len(hypotheses),
        )
        return hypotheses

    def _interpret_single(
        self, summary: AnomalySummary
    ) -> Optional[FaultHypothesis]:
        """
        Send a single AnomalySummary to the LLM and parse the response
        into a FaultHypothesis using instructor schema enforcement.
        """
        fault_types_str = ", ".join(f'"{ft.value}"' for ft in FaultType)
        system_msg = SYSTEM_PROMPT.format(fault_types=fault_types_str)
        user_msg = USER_PROMPT_TEMPLATE.format(
            anomaly_block=summary.to_prompt_block()
        )

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                client = self._get_instructor_client()
                hypothesis = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    response_model=FaultHypothesis,
                    temperature=self.temperature,
                    max_retries=0,
                )
                hypothesis.source_cluster_id = summary.source_cluster_id
                logger.debug(
                    "Cluster %d -> %s (confidence=%.2f)",
                    summary.source_cluster_id,
                    hypothesis.fault_type.value,
                    hypothesis.confidence,
                )
                return hypothesis

            except Exception as e:
                logger.warning(
                    "LLM parse attempt %d/%d failed for cluster %d: %s",
                    attempt, MAX_RETRIES, summary.source_cluster_id, e,
                )

        logger.error(
            "SKIPPED cluster %d after %d failed attempts",
            summary.source_cluster_id, MAX_RETRIES,
        )
        return None

    def interpret_anomalies_raw(
        self, summaries: List[AnomalySummary]
    ) -> List[FaultHypothesis]:
        """
        Fallback path using raw Ollama HTTP API + manual Pydantic parsing.
        Used when instructor is unavailable.
        """
        import json
        import requests

        hypotheses: List[FaultHypothesis] = []
        fault_types_str = ", ".join(f'"{ft.value}"' for ft in FaultType)

        for summary in summaries:
            system_msg = SYSTEM_PROMPT.format(fault_types=fault_types_str)
            user_msg = USER_PROMPT_TEMPLATE.format(
                anomaly_block=summary.to_prompt_block()
            )

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    resp = requests.post(
                        f"{self.base_url}/api/chat",
                        json={
                            "model": self.model,
                            "messages": [
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": user_msg},
                            ],
                            "format": "json",
                            "stream": False,
                            "options": {"temperature": self.temperature},
                        },
                        timeout=120,
                    )
                    resp.raise_for_status()
                    content = resp.json()["message"]["content"]
                    parsed = json.loads(content)
                    parsed["source_cluster_id"] = summary.source_cluster_id
                    hypothesis = FaultHypothesis.model_validate(parsed)
                    hypotheses.append(hypothesis)
                    break
                except Exception as e:
                    logger.warning(
                        "Raw LLM attempt %d/%d failed for cluster %d: %s",
                        attempt, MAX_RETRIES, summary.source_cluster_id, e,
                    )

        return hypotheses

    def health_check(self) -> bool:
        """Verify Ollama is reachable and the model is available."""
        try:
            import requests
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            models = [m["name"] for m in resp.json().get("models", [])]
            available = any(self.model in m for m in models)
            if not available:
                logger.warning("Model '%s' not found. Available: %s", self.model, models)
            return available
        except Exception:
            return False
