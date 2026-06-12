"""Tests for project scope guards (microservices-focused pipeline)."""

from chaosgen.config.scope import (
    DISCOVERY_ENABLED,
    FOCUSED_ARCHITECTURE,
    build_focused_discovery_report,
)
from chaosgen.discovery import resolve_discovery_report
from chaosgen.schemas.discovery import ArchitectureType


class TestScope:
    def test_discovery_disabled_by_default(self):
        assert DISCOVERY_ENABLED is False

    def test_focused_architecture_is_microservices(self):
        assert FOCUSED_ARCHITECTURE == ArchitectureType.MICROSERVICES

    def test_focused_report_is_microservices(self):
        report = build_focused_discovery_report()
        assert report.architecture.type == ArchitectureType.MICROSERVICES
        assert report.service_map.nodes
        assert report.observability.has_metrics
        assert report.observability.has_logs

    def test_resolve_skips_full_discovery(self):
        report = resolve_discovery_report()
        assert report.architecture.type == ArchitectureType.MICROSERVICES
        assert any("Discovery disabled" in s.message for s in report.signals)
