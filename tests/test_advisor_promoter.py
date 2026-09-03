"""Focused promote-after-PASS guards (Option B / Oct harden)."""
from __future__ import annotations

import pytest

from chaosgen.advisor.catalog_promoter import CatalogPromoter, PromoteError
from chaosgen.advisor.promoted_store import PromotedStore
from chaosgen.schemas.discovery import ArchitectureType
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    NetworkFaultSpec,
    TargetSpec,
    TargetType,
)
from chaosgen.schemas.scenarios import (
    ExperimentVerdict,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)


def _experiment() -> ChaosExperiment:
    return ChaosExperiment(
        name="exp-pass-gate",
        description="latency",
        target=TargetSpec(type=TargetType.SERVICE, name="payment-api"),
        faults=[
            NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY,
                duration="30s",
                latency="100ms",
            )
        ],
    )


def _described() -> UnknownScenarioDescription:
    return UnknownScenarioDescription(
        title="Payment API latency cascade case",
        root_cause_hypothesis="Upstream timeout exhausts the payment-api thread pool",
        repro_steps=["Inject latency on payment-api", "Observe breaker"],
        blast_radius_estimate="payment-api and downstream checkout",
        suggested_fault_type=FaultType.NETWORK_LATENCY,
        confidence=0.8,
        source_incident_id=1,
        knowledge_state=ScenarioKnowledgeState.DESCRIBED,
    )


@pytest.fixture
def promoter(tmp_path) -> CatalogPromoter:
    return CatalogPromoter(store=PromotedStore(path=tmp_path / "promoted_scenarios.json"))


class TestPromoteAfterPass:
    def test_pass_promotes_to_known(self, promoter):
        desc = _described()
        entry = promoter.promote(
            desc,
            _experiment(),
            approved_by="op",
            verdict=ExperimentVerdict.PASS,
            architecture=ArchitectureType.MODULAR_MONOLITH,
        )
        assert entry.architecture == ArchitectureType.MODULAR_MONOLITH
        assert entry.source == "promoted"
        assert desc.knowledge_state == ScenarioKnowledgeState.KNOWN

    def test_fail_keeps_described(self, promoter):
        desc = _described()
        with pytest.raises(PromoteError, match="PASS"):
            promoter.promote(
                desc, _experiment(), approved_by="op", verdict=ExperimentVerdict.FAIL
            )
        assert desc.knowledge_state == ScenarioKnowledgeState.DESCRIBED
        assert promoter._store.load() == []

    def test_partial_keeps_described(self, promoter):
        desc = _described()
        with pytest.raises(PromoteError, match="PASS"):
            promoter.promote(
                desc, _experiment(), approved_by="op", verdict=ExperimentVerdict.PARTIAL
            )
        assert desc.knowledge_state == ScenarioKnowledgeState.DESCRIBED
