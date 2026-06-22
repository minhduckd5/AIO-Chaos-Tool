"""Tests for P3 — Promote Known Catalog (promoted store + promoter + merge)."""
from __future__ import annotations

import json
import threading

import pytest

from chaosgen.advisor.catalog_promoter import (
    CatalogPromoter,
    PromoteError,
    evaluate_acceptance,
    validate_acceptance_criteria,
)
from chaosgen.advisor.promoted_store import (
    SCHEMA_VERSION,
    PromotedCatalogRecord,
    PromotedStore,
    experiment_from_dict,
    experiment_to_dict,
)
from chaosgen.advisor.scenario_catalog import ScenarioCatalog
from chaosgen.schemas.discovery import ArchitectureType
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    NetworkFaultSpec,
    ResourceFaultSpec,
    TargetSpec,
    TargetType,
)
from chaosgen.schemas.scenarios import (
    ExperimentVerdict,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)

MS = ArchitectureType.MICROSERVICES


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


def _experiment(name: str = "exp-1") -> ChaosExperiment:
    return ChaosExperiment(
        name=name,
        description="2s latency injection",
        target=TargetSpec(type=TargetType.SERVICE, name="payment-api"),
        faults=[
            NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY,
                duration="60s",
                latency="2000ms",
                jitter="200ms",
            )
        ],
        rollback=True,
    )


def _description(
    incident_id: int = 42,
    title: str = "Payment API latency cascade",
    knowledge_state: ScenarioKnowledgeState = ScenarioKnowledgeState.DESCRIBED,
    fallback: bool = False,
) -> UnknownScenarioDescription:
    return UnknownScenarioDescription(
        title=title,
        root_cause_hypothesis="Upstream timeout exhausts the payment-api thread pool",
        repro_steps=["Inject 2s latency on payment-api", "Observe circuit breaker"],
        blast_radius_estimate="payment-api and downstream checkout",
        suggested_fault_type=FaultType.NETWORK_LATENCY,
        confidence=0.8,
        source_incident_id=incident_id,
        knowledge_state=knowledge_state,
        metadata={"describe_fallback": True} if fallback else {},
    )


@pytest.fixture
def store(tmp_path) -> PromotedStore:
    return PromotedStore(path=tmp_path / "promoted_scenarios.json")


@pytest.fixture
def promoter(store) -> CatalogPromoter:
    return CatalogPromoter(store=store)


# ---------------------------------------------------------------------------
# Experiment (de)serialization — polymorphic faults
# ---------------------------------------------------------------------------


class TestExperimentSerialization:
    def test_roundtrip_preserves_network_subclass_fields(self):
        exp = _experiment()
        rebuilt = experiment_from_dict(experiment_to_dict(exp))
        fault = rebuilt.faults[0]
        assert isinstance(fault, NetworkFaultSpec)
        assert fault.latency == "2000ms"
        assert fault.jitter == "200ms"

    def test_roundtrip_resource_subclass(self):
        exp = ChaosExperiment(
            name="oom",
            target=TargetSpec(type=TargetType.CONTAINER, name="svc"),
            faults=[ResourceFaultSpec(fault_type=FaultType.RESOURCE_EXHAUSTION, memory_percent=95)],
        )
        rebuilt = experiment_from_dict(experiment_to_dict(exp))
        assert isinstance(rebuilt.faults[0], ResourceFaultSpec)
        assert rebuilt.faults[0].memory_percent == 95


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------


class TestStoreIO:
    def test_append_and_reload_roundtrip(self, store, promoter):
        promoter.promote(_description(), _experiment(), approved_by="operator")
        reloaded = PromotedStore(path=store.path).load()
        assert len(reloaded) == 1
        assert reloaded[0].name == "Payment API latency cascade"
        assert reloaded[0].approved_by == "operator"

    def test_envelope_is_versioned(self, store, promoter):
        promoter.promote(_description(), _experiment(), approved_by="operator")
        raw = json.loads(store.path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == SCHEMA_VERSION
        assert isinstance(raw["scenarios"], list)

    def test_atomic_write_leaves_no_tmp_orphan(self, store, promoter):
        promoter.promote(_description(), _experiment(), approved_by="operator")
        orphans = list(store.path.parent.glob("*.tmp"))
        assert orphans == []

    def test_successful_write_rotates_backup(self, store, promoter):
        promoter.promote(_description(incident_id=1, title="First scenario one"), _experiment(), approved_by="op")
        promoter.promote(_description(incident_id=2, title="Second scenario two"), _experiment(), approved_by="op")
        # .bak holds the pre-second-write state (one record).
        bak = json.loads(store.bak_path.read_text(encoding="utf-8"))
        assert len(bak["scenarios"]) == 1


# ---------------------------------------------------------------------------
# Corruption recovery
# ---------------------------------------------------------------------------


class TestRecovery:
    def test_corrupt_main_recovers_from_backup(self, store, promoter):
        promoter.promote(_description(incident_id=1, title="Good scenario one"), _experiment(), approved_by="op")
        promoter.promote(_description(incident_id=2, title="Good scenario two"), _experiment(), approved_by="op")
        # Corrupt the live file; .bak still holds the one-record snapshot.
        store.path.write_text("{ this is not json", encoding="utf-8")

        recovered = store.load_records_safe()
        assert len(recovered) == 1
        assert recovered[0].name == "Good scenario one"
        # Corrupt file is quarantined.
        assert list(store.path.parent.glob("*.corrupt.*"))

    def test_corrupt_main_and_backup_returns_empty(self, store, promoter):
        promoter.promote(_description(), _experiment(), approved_by="op")
        store.path.write_text("garbage", encoding="utf-8")
        store.bak_path.write_text("also garbage", encoding="utf-8")

        assert store.load_records_safe() == []

    def test_catalog_never_crashes_on_corrupt_store(self, store):
        store.path.write_text("totally broken", encoding="utf-8")
        catalog = ScenarioCatalog(promoted_store=store)
        # Built-in microservices entries must still come through.
        entries = catalog.get_all(MS)
        assert len(entries) > 0
        assert all(e.source == "builtin" for e in entries)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_parallel_promotes_no_lost_entries(self, store):
        total = 40

        def worker(i: int) -> None:
            promoter = CatalogPromoter(store=store)
            promoter.promote(
                _description(incident_id=i, title=f"Concurrent scenario number {i}"),
                _experiment(name=f"exp-{i}"),
                approved_by="op",
            )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        records = PromotedStore(path=store.path).load()
        assert len(records) == total
        # File is valid JSON with a complete envelope.
        raw = json.loads(store.path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Schema compatibility / migration
# ---------------------------------------------------------------------------


def _record_dict(incident_id: int, name: str) -> dict:
    return PromotedCatalogRecord(
        name=name,
        description="root cause",
        architecture=MS,
        fault_type=FaultType.NETWORK_LATENCY,
        experiment_spec=experiment_to_dict(_experiment()),
        promoted_at="2026-06-22T10:00:00Z",
        approved_by="op",
        source_incident_id=incident_id,
    ).model_dump(mode="json")


class TestSchemaCompat:
    def test_load_legacy_bare_list(self, store):
        store.path.write_text(json.dumps([_record_dict(1, "Legacy bare scenario")]), encoding="utf-8")
        records = store.load()
        assert len(records) == 1
        assert records[0].name == "Legacy bare scenario"

    def test_load_v1_envelope(self, store):
        envelope = {"schema_version": "1.0", "scenarios": [_record_dict(1, "V1 scenario entry")]}
        store.path.write_text(json.dumps(envelope), encoding="utf-8")
        records = store.load()
        assert len(records) == 1

    def test_unsupported_version_raises(self, store):
        envelope = {"schema_version": "99.0", "scenarios": []}
        store.path.write_text(json.dumps(envelope), encoding="utf-8")
        with pytest.raises(Exception):
            store.load()


# ---------------------------------------------------------------------------
# Promoter guards
# ---------------------------------------------------------------------------


class TestPromoterGuards:
    def test_reject_fallback_description(self, promoter):
        with pytest.raises(PromoteError, match="fallback"):
            promoter.promote(
                _description(fallback=True), _experiment(), approved_by="op"
            )

    def test_reject_non_described_state(self, promoter):
        with pytest.raises(PromoteError, match="DESCRIBED"):
            promoter.promote(
                _description(knowledge_state=ScenarioKnowledgeState.UNKNOWN),
                _experiment(),
                approved_by="op",
            )

    def test_reject_empty_approved_by(self, promoter):
        with pytest.raises(PromoteError, match="HITL"):
            promoter.promote(_description(), _experiment(), approved_by="  ")

    def test_reject_duplicate_name(self, promoter):
        promoter.promote(_description(incident_id=1), _experiment(), approved_by="op")
        with pytest.raises(PromoteError, match="duplicate"):
            promoter.promote(_description(incident_id=2), _experiment(), approved_by="op")

    def test_reject_duplicate_incident(self, promoter):
        promoter.promote(_description(incident_id=7, title="Scenario alpha one"), _experiment(), approved_by="op")
        with pytest.raises(PromoteError, match="already promoted"):
            promoter.promote(_description(incident_id=7, title="Scenario beta two"), _experiment(), approved_by="op")

    def test_reject_invalid_acceptance_criteria(self, promoter):
        with pytest.raises(PromoteError, match="acceptance_criteria"):
            promoter.promote(
                _description(), _experiment(), approved_by="op",
                acceptance_criteria={"http_health": "not-a-url"},
            )

    def test_promote_sets_known_state(self, promoter):
        desc = _description()
        promoter.promote(desc, _experiment(), approved_by="op")
        assert desc.knowledge_state == ScenarioKnowledgeState.KNOWN


# ---------------------------------------------------------------------------
# Acceptance criteria validation + evaluation
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    def test_valid_criteria_passes(self):
        errors = validate_acceptance_criteria(
            {
                "http_health": "http://api-gateway:8080/health",
                "prometheus": {"url": "http://prometheus:9090", "query": 'up{job="api"}'},
            }
        )
        assert errors == []

    def test_bad_http_url(self):
        assert validate_acceptance_criteria({"http_health": "ftp://x"})

    def test_empty_promql(self):
        assert validate_acceptance_criteria({"prometheus": {"query": "  "}})

    def test_unbalanced_braces(self):
        assert validate_acceptance_criteria({"prometheus": {"query": 'up{job="api"'}})

    def test_none_criteria_ok(self):
        assert validate_acceptance_criteria(None) == []

    def test_evaluate_passes_with_no_criteria(self):
        assert evaluate_acceptance(None) == ExperimentVerdict.PASS

    def test_evaluate_uses_validator(self):
        class _FakeValidator:
            def __init__(self, result):
                self._result = result

            def validate(self, _criteria):
                return self._result

        criteria = {"http_health": "http://x/health"}
        assert evaluate_acceptance(criteria, _FakeValidator(True)) == ExperimentVerdict.PASS
        assert evaluate_acceptance(criteria, _FakeValidator(False)) == ExperimentVerdict.FAIL


# ---------------------------------------------------------------------------
# Catalog merge
# ---------------------------------------------------------------------------


class TestCatalogMerge:
    def test_promoted_appears_in_get_all(self, store, promoter):
        before = len(ScenarioCatalog(promoted_store=store).get_all(MS))
        promoter.promote(_description(), _experiment(), approved_by="op")
        entries = ScenarioCatalog(promoted_store=store).get_all(MS)
        assert len(entries) == before + 1

        promoted = [e for e in entries if e.source == "promoted"]
        assert len(promoted) == 1
        # Promoted entry rebuilds a working experiment.
        built = promoted[0].build()
        assert built.faults[0].latency == "2000ms"

    def test_source_tags_are_correct(self, store, promoter):
        promoter.promote(_description(), _experiment(), approved_by="op")
        entries = ScenarioCatalog(promoted_store=store).get_all(MS)
        sources = {e.source for e in entries}
        assert sources == {"builtin", "promoted"}

    def test_fault_type_filter_includes_promoted(self, store, promoter):
        promoter.promote(_description(), _experiment(), approved_by="op")
        catalog = ScenarioCatalog(promoted_store=store)
        latency = catalog.get(MS, FaultType.NETWORK_LATENCY)
        assert any(e.source == "promoted" for e in latency)
