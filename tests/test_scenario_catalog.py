"""
Tests for ScenarioCatalog.
"""

from __future__ import annotations

import pytest

from chaosgen.advisor.scenario_catalog import ScenarioCatalog
from chaosgen.schemas.discovery import ArchitectureType
from chaosgen.schemas.faults import ChaosExperiment, FaultType


class TestScenarioCatalog:
    @pytest.fixture
    def catalog(self):
        return ScenarioCatalog()

    def test_catalog_non_empty(self, catalog):
        assert len(catalog) > 0

    def test_get_microservices_returns_entries(self, catalog):
        entries = catalog.get(ArchitectureType.MICROSERVICES)
        assert len(entries) >= 1
        for e in entries:
            assert e.architecture == ArchitectureType.MICROSERVICES

    def test_get_with_fault_type_filter(self, catalog):
        entries = catalog.get(ArchitectureType.MICROSERVICES, fault_type=FaultType.NETWORK_LATENCY)
        for e in entries:
            assert e.fault_type == FaultType.NETWORK_LATENCY

    def test_get_all_returns_same_as_get(self, catalog):
        all_entries = catalog.get_all(ArchitectureType.MONOLITH)
        get_entries = catalog.get(ArchitectureType.MONOLITH)
        assert len(all_entries) == len(get_entries)

    def test_search_by_keyword(self, catalog):
        results = catalog.search("kafka")
        assert len(results) >= 1
        for r in results:
            assert (
                "kafka" in r.name.lower()
                or "kafka" in r.description.lower()
                or any("kafka" in tag for tag in r.tags)
            )

    def test_search_case_insensitive(self, catalog):
        lower = catalog.search("circuit-breaker")
        upper = catalog.search("CIRCUIT-BREAKER")
        assert len(lower) == len(upper)

    def test_build_returns_chaos_experiment(self, catalog):
        entry = catalog.get(ArchitectureType.MICROSERVICES)[0]
        exp = entry.build()
        assert isinstance(exp, ChaosExperiment)
        assert exp.rollback is True

    def test_all_entries_buildable(self, catalog):
        for entry in catalog._entries:
            exp = entry.build()
            assert isinstance(exp, ChaosExperiment)

    def test_get_modular_monolith_returns_entries(self, catalog):
        entries = catalog.get(ArchitectureType.MODULAR_MONOLITH)
        assert len(entries) >= 2
        for e in entries:
            assert e.architecture == ArchitectureType.MODULAR_MONOLITH

    def test_all_architectures_represented(self, catalog):
        architectures = catalog.all_architectures()
        assert ArchitectureType.MODULAR_MONOLITH in architectures
        assert len(architectures) >= 4

    def test_get_by_tags(self, catalog):
        results = catalog.get_by_tags("timeout")
        for r in results:
            assert "timeout" in r.tags
