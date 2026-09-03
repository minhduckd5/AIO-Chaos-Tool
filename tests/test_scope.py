"""Tests for form-first profile mode and scope guards (WS-1)."""

import pytest

from chaosgen.config.profile_presets import (
    P0_LIVE_ARCHITECTURES,
    P1_DRY_RUN_ARCHITECTURES,
    build_preset_discovery_report,
    default_environment_for,
    profile_priority_tier,
)
from chaosgen.config.scope import (
    DISCOVERY_ENABLED,
    FOCUSED_ARCHITECTURE,
    build_focused_discovery_report,
    scope_notice,
)
from chaosgen.config.settings import ChaosGenSettings, UserHints
from chaosgen.discovery import resolve_discovery_report
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType


class TestScope:
    def test_discovery_disabled_by_default(self):
        assert DISCOVERY_ENABLED is False

    def test_focused_architecture_is_microservices(self):
        assert FOCUSED_ARCHITECTURE == ArchitectureType.MICROSERVICES

    def test_scope_notice_mentions_profile_mode(self):
        assert "Profile mode" in scope_notice()
        assert "DISCOVERY_ENABLED=False" in scope_notice()

    def test_focused_report_is_microservices(self):
        report = build_focused_discovery_report()
        assert report.architecture.type == ArchitectureType.MICROSERVICES
        assert report.environment.type == EnvironmentType.KUBERNETES
        assert report.service_map.nodes
        assert report.observability.has_metrics
        assert report.observability.has_logs

    def test_resolve_default_skips_full_discovery(self):
        report = resolve_discovery_report()
        assert report.architecture.type == ArchitectureType.MICROSERVICES
        assert any("Profile mode" in s.message or "profile" in s.message.lower() for s in report.signals)


class TestProfilePresets:
    @pytest.mark.parametrize(
        "architecture,expected_env",
        [
            (ArchitectureType.MICROSERVICES, EnvironmentType.KUBERNETES),
            (ArchitectureType.MODULAR_MONOLITH, EnvironmentType.DOCKER_COMPOSE),
            (ArchitectureType.MONOLITH, EnvironmentType.DOCKER_COMPOSE),
            (ArchitectureType.EVENT_DRIVEN, EnvironmentType.DOCKER_COMPOSE),
            (ArchitectureType.CLIENT_SERVER, EnvironmentType.DOCKER_COMPOSE),
            (ArchitectureType.SERVERLESS, EnvironmentType.SERVERLESS),
        ],
    )
    def test_preset_default_environment(self, architecture, expected_env):
        assert default_environment_for(architecture) == expected_env
        report = build_preset_discovery_report(architecture)
        assert report.architecture.type == architecture
        assert report.environment.type == expected_env
        assert report.service_map.nodes

    def test_microservices_service_map_matches_catalog_targets(self):
        report = build_preset_discovery_report(ArchitectureType.MICROSERVICES)
        names = {n.name for n in report.service_map.nodes}
        assert "api-gateway" in names
        assert "downstream-service" in names

    def test_modular_monolith_is_p0(self):
        assert profile_priority_tier(ArchitectureType.MODULAR_MONOLITH) == "P0"
        assert ArchitectureType.MODULAR_MONOLITH in P0_LIVE_ARCHITECTURES

    def test_event_driven_is_p1_with_broker_signal(self):
        assert profile_priority_tier(ArchitectureType.EVENT_DRIVEN) == "P1"
        assert ArchitectureType.EVENT_DRIVEN in P1_DRY_RUN_ARCHITECTURES
        report = build_preset_discovery_report(ArchitectureType.EVENT_DRIVEN)
        assert report.architecture.has_message_broker is True

    def test_service_override_extends_map(self):
        report = build_preset_discovery_report(
            ArchitectureType.MONOLITH,
            service_overrides=["custom-worker"],
        )
        names = {n.name for n in report.service_map.nodes}
        assert "monolith-app" in names
        assert "custom-worker" in names


class TestResolveFromHints:
    def test_resolve_modular_monolith_from_settings(self):
        settings = ChaosGenSettings(
            hints=UserHints(
                architecture=ArchitectureType.MODULAR_MONOLITH,
                environment=EnvironmentType.DOCKER_COMPOSE,
                skip_auto_detect=True,
            ),
        )
        report = resolve_discovery_report(settings=settings)
        assert report.architecture.type == ArchitectureType.MODULAR_MONOLITH
        assert report.environment.type == EnvironmentType.DOCKER_COMPOSE
        assert any(s.key == "profile_tier" and s.value == "P0" for s in report.signals)

    def test_resolve_event_driven_without_explicit_env(self):
        settings = ChaosGenSettings(
            hints=UserHints(
                architecture=ArchitectureType.EVENT_DRIVEN,
                skip_auto_detect=True,
            ),
        )
        report = resolve_discovery_report(settings=settings)
        assert report.architecture.type == ArchitectureType.EVENT_DRIVEN
        assert report.environment.type == EnvironmentType.DOCKER_COMPOSE

    def test_no_hints_still_defaults_microservices(self):
        settings = ChaosGenSettings(hints=UserHints())
        report = resolve_discovery_report(settings=settings)
        assert report.architecture.type == ArchitectureType.MICROSERVICES
