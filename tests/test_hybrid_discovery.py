"""
Tests for the Hybrid Discovery Refactor — settings, auth, probers,
merge engine, XDG migration, secret-leak guard, error classification.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ======================================================================
# Phase 1: XDG paths and settings
# ======================================================================


class TestPaths:
    def test_config_dir_is_xdg_on_linux(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)

        import importlib
        import chaosgen.config.paths as paths_mod
        importlib.reload(paths_mod)

        assert ".config" in str(paths_mod._resolve_config_dir()) or "chaosgen" in str(paths_mod._resolve_config_dir())

    def test_config_dir_uses_xdg_env(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test_xdg")

        import importlib
        import chaosgen.config.paths as paths_mod
        importlib.reload(paths_mod)

        assert str(paths_mod._resolve_config_dir()) == str(Path("/tmp/test_xdg/chaosgen"))

    def test_ensure_config_dir_creates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("chaosgen.config.paths.CONFIG_DIR", tmp_path / "new_cfg")
        monkeypatch.setattr("chaosgen.config.paths._LEGACY_DIR", tmp_path / "no_legacy")

        from chaosgen.config.paths import ensure_config_dir
        result = ensure_config_dir()
        assert result.exists()

    def test_legacy_migration(self, tmp_path, monkeypatch):
        legacy = tmp_path / ".chaosgen"
        legacy.mkdir()
        (legacy / ".env").write_text("OPENAI_API_KEY=sk-test123\n")

        new_dir = tmp_path / "config" / "chaosgen"
        monkeypatch.setattr("chaosgen.config.paths._LEGACY_DIR", legacy)
        monkeypatch.setattr("chaosgen.config.paths.CONFIG_DIR", new_dir)

        from chaosgen.config.paths import ensure_config_dir
        ensure_config_dir()

        assert (new_dir / ".env").exists()
        assert (legacy / "_MIGRATED_TO").exists()


# ======================================================================
# Phase 1b: Settings + secret-leak guard
# ======================================================================


class TestSettings:
    def test_load_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("chaosgen.config.settings.SETTINGS_FILE", tmp_path / "nope.yaml")

        from chaosgen.config.settings import load_settings
        s = load_settings()
        assert s.llm_provider == "ollama"
        assert s.hints.architecture is None

    def test_load_from_yaml(self, tmp_path):
        from chaosgen.config.settings import load_settings
        from chaosgen.schemas.discovery import ArchitectureType

        cfg = {
            "hints": {"architecture": "microservices", "environment": "kubernetes"},
            "llm_provider": "openai",
        }
        f = tmp_path / "settings.yaml"
        f.write_text(yaml.dump(cfg))

        s = load_settings(str(f))
        assert s.hints.architecture == ArchitectureType.MICROSERVICES
        assert s.llm_provider == "openai"

    def test_save_and_reload(self, tmp_path, monkeypatch):
        monkeypatch.setattr("chaosgen.config.settings.SETTINGS_FILE", tmp_path / "settings.yaml")
        monkeypatch.setattr("chaosgen.config.paths.CONFIG_DIR", tmp_path)

        from chaosgen.config.settings import ChaosGenSettings, UserHints, save_settings, load_settings
        from chaosgen.schemas.discovery import ArchitectureType

        s = ChaosGenSettings(
            hints=UserHints(architecture=ArchitectureType.MONOLITH),
            llm_provider="groq",
        )
        save_settings(s, str(tmp_path / "settings.yaml"))

        loaded = load_settings(str(tmp_path / "settings.yaml"))
        assert loaded.hints.architecture == ArchitectureType.MONOLITH
        assert loaded.llm_provider == "groq"

    def test_secret_leak_guard_detects_token_prefix(self, tmp_path):
        from chaosgen.config.settings import InlineSecretError, _check_no_inline_secrets

        raw = {
            "hints": {
                "observability": [
                    {
                        "tool": "prometheus",
                        "url": "http://localhost:9090",
                        "auth": {"auth_type": "bearer", "token_ref": "sk-proj-abc123"},
                    }
                ]
            }
        }
        with pytest.raises(InlineSecretError, match="known secret prefix"):
            _check_no_inline_secrets(raw)

    def test_secret_leak_guard_detects_long_value(self, tmp_path):
        from chaosgen.config.settings import InlineSecretError, _check_no_inline_secrets

        raw = {
            "hints": {
                "observability": [
                    {
                        "tool": "loki",
                        "url": "http://localhost:3100",
                        "auth": {"auth_type": "bearer", "token_ref": "x" * 60},
                    }
                ]
            }
        }
        with pytest.raises(InlineSecretError, match="inline secret"):
            _check_no_inline_secrets(raw)

    def test_secret_leak_guard_passes_valid_ref(self):
        from chaosgen.config.settings import _check_no_inline_secrets

        raw = {
            "hints": {
                "observability": [
                    {
                        "tool": "prometheus",
                        "url": "http://localhost:9090",
                        "auth": {"auth_type": "bearer", "token_ref": "PROMETHEUS_TOKEN"},
                    }
                ]
            }
        }
        _check_no_inline_secrets(raw)


# ======================================================================
# Phase 2a: Schema types
# ======================================================================


class TestSchemaTypes:
    def test_probe_outcome_enum_values(self):
        from chaosgen.schemas.discovery import ProbeOutcome
        assert ProbeOutcome.REACHABLE.value == "reachable"
        assert ProbeOutcome.AUTH_REJECTED.value == "auth_rejected"

    def test_discovery_signal_model(self):
        from chaosgen.schemas.discovery import DiscoverySignal, HintSource
        s = DiscoverySignal(
            source=HintSource.USER_OVERRIDE,
            key="architecture_type",
            value="microservices",
            flag="USER_HEURISTIC_MISMATCH",
            message="user says micro, auto says mono",
        )
        assert s.flag == "USER_HEURISTIC_MISMATCH"
        assert s.confidence == 1.0

    def test_discovery_report_summary_includes_mismatch(self):
        from chaosgen.schemas.discovery import (
            ArchitectureProfile,
            ArchitectureType,
            DiscoveryReport,
            DiscoverySignal,
            EnvironmentProfile,
            EnvironmentType,
            HintSource,
            ObservabilityProfile,
            ServiceMap,
        )
        report = DiscoveryReport(
            environment=EnvironmentProfile(type=EnvironmentType.KUBERNETES),
            architecture=ArchitectureProfile(type=ArchitectureType.MICROSERVICES),
            service_map=ServiceMap(),
            observability=ObservabilityProfile(),
            signals=[
                DiscoverySignal(
                    source=HintSource.USER_OVERRIDE,
                    key="architecture_type",
                    value="microservices",
                    flag="USER_HEURISTIC_MISMATCH",
                    message="user=micro, auto=mono",
                ),
            ],
        )
        txt = report.summary_text()
        assert "WARNINGS" in txt
        assert "user=micro, auto=mono" in txt


# ======================================================================
# Phase 2b: Observability probers
# ======================================================================


class TestObservabilityProbers:
    def test_prometheus_prober_reachable(self):
        from chaosgen.discovery.observability_probe import PrometheusProber, ProbeResult, ProbeOutcome
        import requests

        prober = PrometheusProber()
        session = requests.Session()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "data": {"result": []}}

        with patch.object(session, "get", return_value=mock_resp):
            result = prober.probe("http://localhost:9090", session)

        assert result.reachable is True
        assert result.outcome == ProbeOutcome.REACHABLE

    def test_prometheus_prober_auth_rejected(self):
        from chaosgen.discovery.observability_probe import PrometheusProber, ProbeOutcome
        import requests

        prober = PrometheusProber()
        session = requests.Session()

        mock_resp = MagicMock()
        mock_resp.status_code = 403

        with patch.object(session, "get", return_value=mock_resp):
            result = prober.probe("http://localhost:9090", session)

        assert result.reachable is False
        assert result.outcome == ProbeOutcome.AUTH_REJECTED

    def test_backoff_skips_retry_on_auth_rejected(self):
        from chaosgen.discovery.observability_probe import (
            PrometheusProber,
            ProbeOutcome,
            _probe_with_backoff,
        )
        import requests

        prober = PrometheusProber()
        session = requests.Session()

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        call_count = 0

        def counting_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_resp

        with patch.object(session, "get", side_effect=counting_get):
            result = _probe_with_backoff(prober, "http://localhost:9090", session, max_retries=3)

        assert result.outcome == ProbeOutcome.AUTH_REJECTED
        assert call_count == 1

    def test_full_probe_returns_signals(self):
        from chaosgen.discovery.observability_probe import ObservabilityProbe
        import requests

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success"}

        with patch("chaosgen.discovery.observability_probe.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            MockSession.return_value = mock_session

            probe = ObservabilityProbe()
            profile, signals = probe.probe()

        assert len(signals) > 0
        assert profile is not None


# ======================================================================
# Phase 2c: Architecture classifier mismatch
# ======================================================================


class TestArchitectureClassifierMismatch:
    def test_mismatch_signal_emitted(self, tmp_path):
        from chaosgen.discovery.architecture_classifier import ArchitectureClassifier
        from chaosgen.schemas.discovery import ArchitectureType, EnvironmentProfile, EnvironmentType

        compose = tmp_path / "docker-compose.yml"
        compose.write_text(yaml.dump({"services": {"app": {"image": "myapp"}}}))

        env = EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE)
        classifier = ArchitectureClassifier(env_profile=env, compose_file=str(compose))

        result = classifier.classify(user_hint=ArchitectureType.MICROSERVICES)

        assert result.type == ArchitectureType.MICROSERVICES
        mismatch_signals = [s for s in result.signals if "USER_HEURISTIC_MISMATCH" in s]
        assert len(mismatch_signals) >= 1

    def test_matching_hint_boosts_confidence(self, tmp_path):
        from chaosgen.discovery.architecture_classifier import ArchitectureClassifier
        from chaosgen.schemas.discovery import ArchitectureType, EnvironmentProfile, EnvironmentType

        compose = tmp_path / "docker-compose.yml"
        compose.write_text(yaml.dump({"services": {"app": {"image": "myapp"}}}))

        env = EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE)
        classifier = ArchitectureClassifier(env_profile=env, compose_file=str(compose))

        without_hint = classifier._auto_classify()
        with_hint = classifier.classify(user_hint=without_hint.type)

        assert with_hint.confidence >= without_hint.confidence


# ======================================================================
# Phase 3: Merge engine (run_full_discovery with settings)
# ======================================================================


class TestMergeEngine:
    def test_user_hint_overrides_env(self, tmp_path, monkeypatch):
        from chaosgen.config.settings import ChaosGenSettings, UserHints
        from chaosgen.schemas.discovery import EnvironmentType
        import requests as req

        monkeypatch.chdir(tmp_path)

        settings = ChaosGenSettings(
            hints=UserHints(environment=EnvironmentType.KUBERNETES),
        )

        with patch("chaosgen.discovery.observability_probe.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.side_effect = req.ConnectionError
            MockSession.return_value = mock_session

            from chaosgen.discovery import run_full_discovery
            report = run_full_discovery(settings=settings)

        assert report.environment.type == EnvironmentType.KUBERNETES
        env_signals = [s for s in report.signals if s.key == "environment_type"]
        assert len(env_signals) >= 1
        assert env_signals[0].source.value == "user_override"

    def test_skip_auto_detect_uses_hint(self, tmp_path, monkeypatch):
        from chaosgen.config.settings import ChaosGenSettings, UserHints
        from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType
        import requests as req

        monkeypatch.chdir(tmp_path)

        settings = ChaosGenSettings(
            hints=UserHints(
                architecture=ArchitectureType.EVENT_DRIVEN,
                environment=EnvironmentType.BARE_METAL,
                skip_auto_detect=True,
            ),
        )

        with patch("chaosgen.discovery.observability_probe.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.side_effect = req.ConnectionError
            MockSession.return_value = mock_session

            from chaosgen.discovery import run_full_discovery
            report = run_full_discovery(settings=settings)

        assert report.architecture.type == ArchitectureType.EVENT_DRIVEN
        assert report.architecture.confidence == 1.0


# ======================================================================
# Phase 4: Context builder includes signals
# ======================================================================


class TestContextBuilderSignals:
    def test_mismatch_rendered_in_prompt(self):
        from chaosgen.advisor.context_builder import ContextBuilder
        from chaosgen.schemas.discovery import (
            ArchitectureProfile,
            ArchitectureType,
            DiscoveryReport,
            DiscoverySignal,
            EnvironmentProfile,
            EnvironmentType,
            HintSource,
            ObservabilityProfile,
            ProbeOutcome,
            ServiceMap,
        )

        report = DiscoveryReport(
            environment=EnvironmentProfile(type=EnvironmentType.KUBERNETES),
            architecture=ArchitectureProfile(type=ArchitectureType.MICROSERVICES),
            service_map=ServiceMap(),
            observability=ObservabilityProfile(),
            signals=[
                DiscoverySignal(
                    source=HintSource.USER_OVERRIDE,
                    key="architecture_type",
                    value="microservices",
                    flag="USER_HEURISTIC_MISMATCH",
                    message="user=micro, auto=mono",
                ),
                DiscoverySignal(
                    source=HintSource.HEURISTIC_FALLBACK,
                    key="prometheus",
                    value="auth_rejected",
                    probe_outcome=ProbeOutcome.AUTH_REJECTED,
                    message="HTTP 403 at https://metrics.corp.com",
                ),
            ],
        )

        ctx = ContextBuilder(report).build()
        prompt = ctx.to_prompt_text()

        assert "CONFIGURATION WARNINGS" in prompt
        assert "user=micro, auto=mono" in prompt
        assert "AUTH ISSUES" in prompt
        assert "prometheus" in prompt
