"""
Tests for multi-provider LLM advisor — build_provider(), provider selection,
MissingAPIKeyError fallback, and context-aware prompt assembly.

All actual LLM calls are mocked — no network access required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestBuildProvider:
    def test_ollama_is_default_no_key_required(self):
        from chaosgen.advisor.llm_advisor import build_provider, OllamaProvider

        with patch("chaosgen.advisor.llm_advisor.load_secrets", return_value={"OLLAMA_URL": "http://localhost:11434"}):
            provider = build_provider("ollama")
        assert isinstance(provider, OllamaProvider)

    def test_openai_requires_api_key(self):
        """OpenAI raises MissingAPIKeyError when key not configured."""
        from chaosgen.config.secrets import MissingAPIKeyError

        with patch("chaosgen.advisor.llm_advisor.get_key", side_effect=MissingAPIKeyError("openai", "OPENAI_API_KEY")):
            from chaosgen.advisor.llm_advisor import build_provider, OllamaProvider
            # Should fall back to Ollama, not raise
            with patch("chaosgen.advisor.llm_advisor.load_secrets", return_value={"OLLAMA_URL": "http://localhost:11434"}):
                provider = build_provider("openai")
            assert isinstance(provider, OllamaProvider)

    def test_unknown_provider_raises(self):
        from chaosgen.advisor.llm_advisor import build_provider

        with pytest.raises(ValueError, match="Unknown provider"):
            build_provider("badprovider")

    def test_ollama_client_uses_openai_compat_when_from_ollama_missing(self):
        """P0: instructor 1.14+ dropped from_ollama — use OpenAI-compat /v1."""
        from types import SimpleNamespace

        from chaosgen.advisor.llm_advisor import OllamaProvider

        fake_client = object()
        mode = SimpleNamespace(JSON="JSON")
        inst = SimpleNamespace(
            Mode=mode,
            from_openai=MagicMock(return_value=fake_client),
        )
        assert not hasattr(inst, "from_ollama")

        fake_openai_mod = MagicMock()
        fake_openai_cls = MagicMock(return_value=MagicMock(name="OpenAIClient"))
        fake_openai_mod.OpenAI = fake_openai_cls

        provider = OllamaProvider(model="llama3.2:3b", base_url="http://127.0.0.1:11434")
        with patch.dict(
            "sys.modules",
            {"instructor": inst, "openai": fake_openai_mod},
        ):
            client = provider._get_client()

        assert client is fake_client
        fake_openai_cls.assert_called_once()
        kwargs = fake_openai_cls.call_args.kwargs
        assert kwargs["base_url"] == "http://127.0.0.1:11434/v1"
        inst.from_openai.assert_called_once()

    def test_openai_uses_custom_base_url(self):
        from chaosgen.advisor.llm_advisor import OpenAIProvider

        with patch("chaosgen.advisor.llm_advisor.get_key", return_value="sk-test"):
            with patch(
                "chaosgen.advisor.llm_advisor.load_secrets",
                return_value={"OPENAI_BASE_URL": "http://9router.local/v1"},
            ):
                with patch("openai.OpenAI") as mock_openai:
                    with patch("instructor.from_openai", return_value=MagicMock()):
                        OpenAIProvider(model="openclaw-coding")
        mock_openai.assert_called_once_with(
            api_key="sk-test",
            base_url="http://9router.local/v1",
            timeout=60.0,
        )

    def test_openai_probe_fails_fast_when_tcp_unreachable(self):
        from chaosgen.advisor.llm_advisor import OpenAIProvider

        with patch("chaosgen.advisor.llm_advisor.get_key", return_value="sk-test"):
            with patch(
                "chaosgen.advisor.llm_advisor.load_secrets",
                return_value={"OPENAI_BASE_URL": "http://127.0.0.1:1/v1"},
            ):
                with patch(
                    "chaosgen.advisor.llm_advisor._tcp_probe_base_url",
                    return_value=(False, "TCP failed"),
                ):
                    with patch("openai.OpenAI"):
                        with patch("instructor.from_openai", return_value=MagicMock()):
                            provider = OpenAIProvider(model="test-model")

        ok, msg = provider.probe()
        assert ok is False
        assert "TCP failed" in msg

    def test_openai_probe_falls_back_to_chat_when_models_list_fails(self):
        from chaosgen.advisor.llm_advisor import OpenAIProvider

        mock_client = MagicMock()
        mock_client.models.list.side_effect = RuntimeError("not implemented")
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="OK"))]
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("chaosgen.advisor.llm_advisor.get_key", return_value="sk-test"):
            with patch("chaosgen.advisor.llm_advisor.load_secrets", return_value={}):
                with patch("chaosgen.advisor.llm_advisor._tcp_probe_base_url", return_value=(True, "TCP OK")):
                    with patch("openai.OpenAI", return_value=mock_client):
                        with patch("instructor.from_openai", return_value=MagicMock()):
                            provider = OpenAIProvider(model="openclaw-coding")

        ok, msg = provider.probe()
        assert ok is True
        assert "chat OK" in msg
        mock_client.chat.completions.create.assert_called_once()


class TestArchitectureSpecificPrompts:
    def test_microservices_prompt_mentions_cascade(self):
        from chaosgen.advisor.llm_advisor import _build_system_prompt
        from chaosgen.schemas.discovery import ArchitectureType

        prompt = _build_system_prompt(ArchitectureType.MICROSERVICES)
        assert "cascade" in prompt.lower() or "cascading" in prompt.lower()

    def test_event_driven_prompt_mentions_broker(self):
        from chaosgen.advisor.llm_advisor import _build_system_prompt
        from chaosgen.schemas.discovery import ArchitectureType

        prompt = _build_system_prompt(ArchitectureType.EVENT_DRIVEN)
        assert "broker" in prompt.lower()

    def test_monolith_prompt_mentions_resource(self):
        from chaosgen.advisor.llm_advisor import _build_system_prompt
        from chaosgen.schemas.discovery import ArchitectureType

        prompt = _build_system_prompt(ArchitectureType.MONOLITH)
        assert "resource" in prompt.lower() or "memory" in prompt.lower()


class TestLLMAdvisor:
    def test_interpret_anomalies_returns_list(self):
        from chaosgen.advisor.llm_advisor import LLMAdvisor
        from chaosgen.schemas.scenarios import AnomalySummary, FaultHypothesis
        from chaosgen.schemas.faults import FaultType

        mock_provider = MagicMock()
        mock_provider.complete.return_value = FaultHypothesis(
            fault_type=FaultType.NETWORK_LATENCY,
            target_hint="svc-a",
            rationale="Network saturation detected.",
            confidence=0.85,
            source_cluster_id=0,
            suggested_duration="60s",
            suggested_parameters={},
        )

        advisor = LLMAdvisor(provider=mock_provider)
        summaries = [
            AnomalySummary(
                source_cluster_id=0,
                service_name="svc-a",
                severity=0.85,
                time_window="2024-03-10 14:00-14:15 UTC",
                top_features=[("latency_p99", 3.2), ("error_rate", 2.1)],
            )
        ]
        results = advisor.interpret_anomalies(summaries)
        assert len(results) == 1
        assert results[0].fault_type == FaultType.NETWORK_LATENCY

    def test_skips_failed_cluster_after_retries(self):
        from chaosgen.advisor.llm_advisor import LLMAdvisor
        from chaosgen.schemas.scenarios import AnomalySummary

        mock_provider = MagicMock()
        mock_provider.complete.side_effect = RuntimeError("LLM timeout")

        advisor = LLMAdvisor(provider=mock_provider)
        summaries = [
            AnomalySummary(
                source_cluster_id=0,
                service_name="svc-b",
                severity=0.5,
                time_window="2024-03-10 14:00-14:15 UTC",
                top_features=[("cpu_usage", 2.5)],
            )
        ]
        results = advisor.interpret_anomalies(summaries)
        assert results == []

    def test_health_check_delegates_to_provider(self):
        from chaosgen.advisor.llm_advisor import LLMAdvisor

        mock_provider = MagicMock()
        mock_provider.health_check.return_value = True

        advisor = LLMAdvisor(provider=mock_provider)
        assert advisor.health_check() is True
