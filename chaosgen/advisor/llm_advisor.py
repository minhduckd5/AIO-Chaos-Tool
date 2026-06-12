"""
Multi-Provider LLM Advisor — generates FaultHypothesis objects from ScenarioContext.

Providers:
  OllamaProvider   — local inference, default, air-gapped safe (no API key required)
  OpenAIProvider   — GPT-4o via openai SDK
  AnthropicProvider — Claude via anthropic SDK
  GroqProvider     — Fast inference via groq SDK

All providers share the same FaultHypothesis Pydantic output schema enforced
via instructor. Keys are loaded exclusively from chaosgen.config.secrets —
never hardcoded or logged.

Architecture-specific system prompts are injected based on ArchitectureType
from the ScenarioContext so the LLM targets the most relevant fault classes.
"""

from __future__ import annotations

import json
import logging
import socket
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from chaosgen.advisor.context_builder import ScenarioContext
from chaosgen.config.secrets import MissingAPIKeyError, get_key, load_secrets
from chaosgen.schemas.discovery import ArchitectureType
from chaosgen.schemas.faults import FaultType
from chaosgen.schemas.scenarios import AnomalySummary, FaultHypothesis

logger = logging.getLogger(__name__)


def _tcp_probe_base_url(base_url: str | None) -> tuple[bool, str]:
    """Low-level socket check before OpenAI SDK calls (clearer than 'Connection error')."""
    if not base_url:
        return True, "official OpenAI API (no local base URL)"
    parsed = urlparse(base_url.strip())
    host = parsed.hostname
    if not host:
        return False, f"invalid base URL: {base_url!r}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=8):
            pass
        return True, f"TCP reachable {host}:{port}"
    except OSError as exc:
        hint = ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            hint = (
                f" — if 9router runs on this PC, try http://127.0.0.1:{port}/v1 "
                f"instead of {host}; ensure Tailscale/VPN is connected if using a tailnet IP"
            )
        return False, f"TCP failed {host}:{port}: {exc}{hint}"

MAX_RETRIES = 2

# ---------------------------------------------------------------------------
# Architecture-specific system prompt suffixes
# ---------------------------------------------------------------------------

_ARCH_PROMPT_SUFFIXES: dict[ArchitectureType, str] = {
    ArchitectureType.MICROSERVICES: (
        "This is a MICROSERVICES architecture. Prioritize: cascading failure scenarios, "
        "upstream dependency timeouts, circuit breaker validation, retry storm amplification, "
        "partial network partitions between services, and DNS resolution failures."
    ),
    ArchitectureType.MONOLITH: (
        "This is a MONOLITH architecture. Prioritize: resource exhaustion (CPU, memory, disk), "
        "database connection pool saturation, file descriptor limits, GC pause amplification, "
        "and synchronous thread pool starvation."
    ),
    ArchitectureType.MODULAR_MONOLITH: (
        "This is a MODULAR MONOLITH. Prioritize: inter-module coupling failures, "
        "shared resource contention, memory leaks in module boundaries, and "
        "database query timeout cascades."
    ),
    ArchitectureType.EVENT_DRIVEN: (
        "This is an EVENT-DRIVEN architecture. Prioritize: message broker unavailability, "
        "consumer group lag amplification, dead letter queue saturation, "
        "message flood beyond consumer processing capacity, and poison pill message injection."
    ),
    ArchitectureType.CLIENT_SERVER: (
        "This is a CLIENT-SERVER architecture. Prioritize: server overload under concurrent "
        "client connections, network latency between client and server tiers, "
        "session state corruption, and connection pool exhaustion."
    ),
    ArchitectureType.SERVERLESS: (
        "This is a SERVERLESS architecture. Prioritize: cold start timeout scenarios, "
        "concurrency limit exhaustion, downstream dependency failures during warm-up, "
        "event payload size limits, and function timeout under high load."
    ),
}

_BASE_SYSTEM_PROMPT = """\
You are a Chaos Engineering advisor for distributed systems.
Analyze the provided system context and anomaly summaries, then recommend
specific fault injection scenarios that will expose the underlying weaknesses.

{arch_suffix}

You must respond with valid JSON matching this exact schema:
{{
  "fault_type": one of [{fault_types}],
  "target_hint": "service or component name to target",
  "rationale": "1-2 sentence explanation of why this fault matches the context",
  "confidence": float between 0.0 and 1.0,
  "source_cluster_id": integer matching the input cluster ID (use 0 if no cluster),
  "suggested_duration": "duration string e.g. 30s, 1m, 5m",
  "suggested_parameters": {{"key": "value"}}
}}

Confidence calibration:
- 0.9-1.0: Clear causal link between anomaly pattern and fault type
- 0.7-0.89: Strong correlation, likely match
- 0.5-0.69: Moderate correlation, worth investigating
- Below 0.5: Weak signal — omit this hypothesis

Valid fault types: [{fault_types}]
"""

_USER_PROMPT_TEMPLATE = """\
{context_block}

Anomaly cluster to address:
{anomaly_block}

Respond with a single JSON object. Do not include any text outside the JSON.
"""


def _build_system_prompt(arch_type: ArchitectureType) -> str:
    fault_types_str = ", ".join(f'"{ft.value}"' for ft in FaultType)
    suffix = _ARCH_PROMPT_SUFFIXES.get(arch_type, "")
    return _BASE_SYSTEM_PROMPT.format(arch_suffix=suffix, fault_types=fault_types_str)


def _build_user_prompt(context: ScenarioContext, summary: AnomalySummary) -> str:
    return _USER_PROMPT_TEMPLATE.format(
        context_block=context.to_prompt_text(),
        anomaly_block=summary.to_prompt_block(),
    )


# ---------------------------------------------------------------------------
# Provider base class
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstract base for all LLM provider adapters."""

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type,
    ) -> Any:
        """
        Send a completion request and return a validated Pydantic model instance.
        Raises on failure — caller handles retries.
        """

    def health_check(self) -> bool:
        """Override in providers that support health checking."""
        ok, _ = self.probe()
        return ok

    def probe(self) -> tuple[bool, str]:
        """Connectivity probe with a human-readable status message."""
        return True, "OK"


# ---------------------------------------------------------------------------
# Ollama Provider (default — local, no API key required)
# ---------------------------------------------------------------------------


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        model: str = "llama3.2:3b",
        base_url: str | None = None,
        temperature: float = 0.2,
    ) -> None:
        secrets = load_secrets()
        self.base_url = (base_url or secrets.get("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
        self.model = model
        self.temperature = temperature
        self._instructor_client = None

    def _get_client(self):
        if self._instructor_client is None:
            import instructor
            from ollama import Client as OllamaClient  # type: ignore
            self._instructor_client = instructor.from_ollama(
                OllamaClient(host=self.base_url),
                mode=instructor.Mode.JSON,
            )
        return self._instructor_client

    def complete(self, system_prompt: str, user_prompt: str, response_model: type) -> Any:
        client = self._get_client()
        return client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_model=response_model,
            temperature=self.temperature,
            max_retries=0,
        )

    def complete_raw(self, system_prompt: str, user_prompt: str) -> dict:
        """Raw HTTP fallback when instructor is unavailable."""
        import requests
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "format": "json",
                "stream": False,
                "options": {"temperature": self.temperature},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return json.loads(resp.json()["message"]["content"])

    def probe(self) -> tuple[bool, str]:
        try:
            import requests
            resp = requests.get(f"{self.base_url}/api/tags", timeout=10)
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code} from {self.base_url}/api/tags"
            models = [m["name"] for m in resp.json().get("models", [])]
            available = any(self.model in m for m in models)
            if not available:
                return False, f"model {self.model!r} not in Ollama (available: {models[:5]})"
            return True, f"Ollama OK @ {self.base_url} (model={self.model})"
        except Exception as exc:
            return False, str(exc)


# ---------------------------------------------------------------------------
# OpenAI Provider
# ---------------------------------------------------------------------------


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        model: str = "gpt-4o",
        temperature: float = 0.2,
        base_url: str | None = None,
    ) -> None:
        api_key = get_key("OPENAI_API_KEY", provider="openai")  # raises MissingAPIKeyError if absent
        secrets = load_secrets()
        effective_base = (base_url or secrets.get("OPENAI_BASE_URL") or "").strip() or None
        import instructor
        from openai import OpenAI  # type: ignore

        client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": 60.0}
        if effective_base:
            client_kwargs["base_url"] = effective_base.rstrip("/")
        self._openai = OpenAI(**client_kwargs)
        self._client = instructor.from_openai(self._openai)
        self.model = model
        self.temperature = temperature
        self.base_url = effective_base

    def probe(self) -> tuple[bool, str]:
        """
        OpenAI-compatible routers (e.g. 9router) often lack a working /models list.
        Fall back to a minimal chat completion using the configured model/combo name.
        """
        base_label = self.base_url or "https://api.openai.com/v1"
        errors: list[str] = []

        tcp_ok, tcp_msg = _tcp_probe_base_url(self.base_url)
        if not tcp_ok:
            logger.warning("OpenAI TCP probe failed: %s", tcp_msg)
            return False, tcp_msg

        try:
            self._openai.models.list()
            return True, f"models.list OK @ {base_label}"
        except Exception as exc:
            errors.append(f"models.list: {exc}")
            logger.warning("OpenAI models.list failed: %s", exc)

        try:
            resp = self._openai.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Reply with the single word OK."}],
                max_tokens=16,
                temperature=0,
            )
            reply = (resp.choices[0].message.content or "").strip()[:60]
            return True, f"chat OK (model={self.model!r} @ {base_label}, reply={reply!r})"
        except Exception as exc:
            errors.append(f"chat({self.model!r}): {exc}")
            logger.warning("OpenAI chat probe failed: %s", exc)

        detail = "; ".join(errors)
        lowered = detail.lower()
        if "connection" in lowered or "connect" in lowered:
            detail += (
                " — cannot reach host from this PC (check Tailscale/VPN, firewall, "
                "or that 9router is listening on the configured IP:port)"
            )
        elif "401" in lowered or "unauthorized" in lowered:
            detail += " — invalid API key for this router"
        elif "404" in lowered and "model" in lowered:
            detail += f" — model/combo {self.model!r} not found on router"
        return False, detail

    def complete(self, system_prompt: str, user_prompt: str, response_model: type) -> Any:
        return self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_model=response_model,
            temperature=self.temperature,
        )


# ---------------------------------------------------------------------------
# Anthropic Provider
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    def __init__(self, model: str = "claude-haiku-4-5", temperature: float = 0.2) -> None:
        api_key = get_key("ANTHROPIC_API_KEY", provider="anthropic")
        import instructor
        import anthropic  # type: ignore
        self._client = instructor.from_anthropic(anthropic.Anthropic(api_key=api_key))
        self.model = model
        self.temperature = temperature

    def complete(self, system_prompt: str, user_prompt: str, response_model: type) -> Any:
        return self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            response_model=response_model,
        )


# ---------------------------------------------------------------------------
# Groq Provider
# ---------------------------------------------------------------------------


class GroqProvider(LLMProvider):
    def __init__(self, model: str = "llama3-8b-8192", temperature: float = 0.2) -> None:
        api_key = get_key("GROQ_API_KEY", provider="groq")
        import instructor
        from groq import Groq  # type: ignore
        self._client = instructor.from_groq(Groq(api_key=api_key))
        self.model = model
        self.temperature = temperature

    def complete(self, system_prompt: str, user_prompt: str, response_model: type) -> Any:
        return self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_model=response_model,
            temperature=self.temperature,
        )


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

_PROVIDER_NAMES = ("ollama", "openai", "anthropic", "groq")


def build_provider(
    provider: str = "ollama",
    model: str | None = None,
    **kwargs: Any,
) -> LLMProvider:
    """
    Instantiate the requested provider.
    Falls back to OllamaProvider on MissingAPIKeyError for cloud providers.
    """
    provider = provider.lower()
    try:
        if provider == "ollama":
            return OllamaProvider(model=model or "llama3.2:3b", **kwargs)
        if provider == "openai":
            return OpenAIProvider(model=model or "gpt-4o", **kwargs)
        if provider == "anthropic":
            return AnthropicProvider(model=model or "claude-haiku-4-5", **kwargs)
        if provider == "groq":
            return GroqProvider(model=model or "llama3-8b-8192", **kwargs)
        raise ValueError(f"Unknown provider '{provider}'. Choose from: {_PROVIDER_NAMES}")
    except MissingAPIKeyError:
        logger.warning(
            "API key missing for provider '%s'. Falling back to OllamaProvider.", provider
        )
        return OllamaProvider(model=model or "llama3.2:3b")


# ---------------------------------------------------------------------------
# LLMAdvisor — orchestrates provider + prompt building
# ---------------------------------------------------------------------------


class LLMAdvisor:
    """
    Context-aware LLM advisor that maps AnomalySummary objects to
    FaultHypothesis objects using the configured provider.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        provider_name: str = "ollama",
        model: str | None = None,
    ) -> None:
        self._provider = provider or build_provider(provider_name, model=model)

    def interpret_anomalies(
        self,
        summaries: list[AnomalySummary],
        context: ScenarioContext | None = None,
    ) -> list[FaultHypothesis]:
        """
        Process a batch of AnomalySummary objects and return FaultHypothesis objects.
        If ScenarioContext is provided, architecture-specific prompts are used.
        """
        arch = context.architecture_type if context else ArchitectureType.UNKNOWN
        system_prompt = _build_system_prompt(arch)

        hypotheses: list[FaultHypothesis] = []
        for summary in summaries:
            user_prompt = (
                _build_user_prompt(context, summary)
                if context
                else USER_PROMPT_TEMPLATE_LEGACY.format(
                    anomaly_block=summary.to_prompt_block()
                )
            )
            h = self._interpret_single(system_prompt, user_prompt, summary.source_cluster_id)
            if h is not None:
                hypotheses.append(h)

        logger.info(
            "LLM produced %d hypotheses from %d summaries (%d skipped)",
            len(hypotheses), len(summaries), len(summaries) - len(hypotheses),
        )
        return hypotheses

    def _interpret_single(
        self,
        system_prompt: str,
        user_prompt: str,
        cluster_id: int,
    ) -> FaultHypothesis | None:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = self._provider.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=FaultHypothesis,
                )
                result.source_cluster_id = cluster_id
                return result
            except Exception as exc:
                logger.warning(
                    "LLM attempt %d/%d failed for cluster %d: %s",
                    attempt, MAX_RETRIES, cluster_id, exc,
                )
        logger.error("SKIPPED cluster %d after %d failed attempts", cluster_id, MAX_RETRIES)
        return None

    def health_check(self) -> bool:
        return self._provider.health_check()


# Keep backward-compat template for callers that don't pass ScenarioContext
USER_PROMPT_TEMPLATE_LEGACY = """\
Analyze the following anomaly cluster and recommend a chaos fault injection:

{anomaly_block}

Respond with a single JSON object. Do not include any text outside the JSON.
"""
