"""Tests for the ScenarioDescriber (P2 — Unknown Describe Loop)."""
import pytest
from pydantic import BaseModel, ValidationError

from chaosgen.advisor.llm_advisor import LLMProvider
from chaosgen.advisor.scenario_describer import ScenarioDescriber
from chaosgen.ml.gatekeeper import IncidentGatekeeper
from chaosgen.schemas.incidents import IncidentCandidate, IncidentVerdict
from chaosgen.schemas.scenarios import (
    AnomalyCluster,
    AnomalySeverity,
    AnomalySummary,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)


class FakeProvider(LLMProvider):
    """Replays a queue of responses per `complete` call.

    Each item is either an Exception (raised), a BaseModel (returned as-is), or a
    dict that is built into the response_model — mimicking instructor, so invalid
    dicts raise ValidationError exactly as the real provider would.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, system_prompt, user_prompt, response_model):
        self.calls += 1
        if not self._responses:
            raise RuntimeError("FakeProvider exhausted")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            return item
        return response_model(**item)


def _good_payload(service="payments", source_id=0):
    return {
        "title": "Payment API latency spike",
        "root_cause_hypothesis": "Upstream timeout on payments causes request pileup",
        "repro_steps": [
            f"Send sustained load to {service}",
            "Observe p99 latency climb past SLO",
        ],
        "blast_radius_estimate": f"Primary impact: {service} service",
        "suggested_fault_type": "network_latency",
        "confidence": 0.8,
        "source_incident_id": source_id,
    }


def _summary(cluster_id=0, service="payments", error_pattern="error: timeout"):
    return AnomalySummary(
        service_name=service,
        top_features=[("http_latency__payments__p99", 3.2)],
        error_pattern=error_pattern,
        severity=0.75,
        time_window="2024-01-01 00:00 - 00:15 UTC",
        source_cluster_id=cluster_id,
    )


def _real_candidate(cluster_id=0, service="payments"):
    """Build a REAL IncidentCandidate by running through the gatekeeper."""
    gk = IncidentGatekeeper()
    cluster = AnomalyCluster(
        cluster_id=cluster_id,
        severity=AnomalySeverity.HIGH,
        affected_services=[service],
        dominant_features=[("cpu_usage__payments__mean", 2.0)],
        sample_count=30,
    )
    candidates, _ = gk.filter([cluster], 10.0)
    assert candidates and candidates[0].verdict == IncidentVerdict.REAL
    return candidates[0]


def _transient_candidate(cluster_id=0):
    gk = IncidentGatekeeper()
    cluster = AnomalyCluster(
        cluster_id=cluster_id,
        severity=AnomalySeverity.CRITICAL,
        affected_services=["payments"],
        dominant_features=[("cpu_usage__payments__mean", 2.0)],
        sample_count=2,
    )
    candidates, _ = gk.filter([cluster], 10.0)
    assert candidates[0].verdict == IncidentVerdict.TRANSIENT
    return candidates[0]


class TestSchemaValidators:
    def test_valid_object_defaults_to_described(self):
        desc = UnknownScenarioDescription(**_good_payload())
        assert desc.knowledge_state == ScenarioKnowledgeState.DESCRIBED

    def test_repro_steps_below_min_length_rejected(self):
        payload = _good_payload()
        payload["repro_steps"] = ["only one step"]
        with pytest.raises(ValidationError):
            UnknownScenarioDescription(**payload)

    def test_repro_steps_empty_string_rejected(self):
        payload = _good_payload()
        payload["repro_steps"] = ["valid step", "   "]
        with pytest.raises(ValidationError):
            UnknownScenarioDescription(**payload)

    def test_vague_root_cause_rejected(self):
        payload = _good_payload()
        payload["root_cause_hypothesis"] = "maybe the database is slow"
        with pytest.raises(ValidationError):
            UnknownScenarioDescription(**payload)

    def test_technical_prose_not_falsely_flagged(self):
        payload = _good_payload()
        payload["root_cause_hypothesis"] = "Connection pool exhausted under retry storm"
        UnknownScenarioDescription(**payload)


class TestInputContract:
    def test_real_candidate_is_describable(self):
        provider = FakeProvider([_good_payload()])
        describer = ScenarioDescriber(provider=provider)
        candidate = _real_candidate()
        result = describer.describe(candidate, _summary())
        assert result.knowledge_state == ScenarioKnowledgeState.DESCRIBED
        assert result.source_incident_id == candidate.cluster_id
        assert provider.calls == 1

    def test_transient_rejected_before_llm(self):
        provider = FakeProvider([_good_payload()])
        describer = ScenarioDescriber(provider=provider)
        with pytest.raises(ValueError):
            describer.describe(_transient_candidate(), _summary())
        assert provider.calls == 0

    def test_noise_rejected_before_llm(self):
        provider = FakeProvider([_good_payload()])
        describer = ScenarioDescriber(provider=provider)
        noise = IncidentCandidate(
            cluster_id=0,
            frequency=0.1,
            severity=0.1,
            verdict=IncidentVerdict.NOISE,
            rationale="low freq + low severity",
        )
        with pytest.raises(ValueError):
            describer.describe(noise, _summary())
        assert provider.calls == 0


class TestRetryAndFallback:
    def test_bad_output_retried_then_succeeds(self):
        bad = _good_payload()
        bad["repro_steps"] = ["single step only"]  # fails min_length on build
        provider = FakeProvider([bad, _good_payload()])
        describer = ScenarioDescriber(provider=provider, max_retries=2)
        result = describer.describe(_real_candidate(), _summary())
        assert result.knowledge_state == ScenarioKnowledgeState.DESCRIBED
        assert provider.calls == 2

    def test_all_attempts_fail_returns_fallback(self):
        provider = FakeProvider([RuntimeError("llm down"), RuntimeError("llm down")])
        describer = ScenarioDescriber(provider=provider, max_retries=2)
        candidate = _real_candidate()
        result = describer.describe(candidate, _summary())
        assert result.knowledge_state == ScenarioKnowledgeState.UNKNOWN
        assert result.metadata["describe_fallback"] is True
        assert result.confidence == 0.0
        assert result.source_incident_id == candidate.cluster_id
        assert provider.calls == 2

    def test_blast_radius_mismatch_triggers_retry(self):
        mismatch = _good_payload()
        mismatch["blast_radius_estimate"] = "Primary impact: some unrelated component"
        provider = FakeProvider([mismatch, _good_payload()])
        describer = ScenarioDescriber(provider=provider, max_retries=2)
        result = describer.describe(_real_candidate(service="payments"), _summary())
        assert result.knowledge_state == ScenarioKnowledgeState.DESCRIBED
        assert provider.calls == 2


class TestBatchResilience:
    def test_batch_mixes_success_and_fallback_without_crashing(self):
        # cluster 0 succeeds on first try; cluster 1 fails all attempts -> fallback.
        provider = FakeProvider(
            [_good_payload(source_id=0), RuntimeError("down"), RuntimeError("down")]
        )
        describer = ScenarioDescriber(provider=provider, max_retries=2)

        gk = IncidentGatekeeper()
        clusters = [
            AnomalyCluster(
                cluster_id=0, severity=AnomalySeverity.HIGH,
                affected_services=["payments"],
                dominant_features=[("cpu_usage__payments__mean", 2.0)],
                sample_count=30,
            ),
            AnomalyCluster(
                cluster_id=1, severity=AnomalySeverity.HIGH,
                affected_services=["payments"],
                dominant_features=[("cpu_usage__payments__mean", 2.0)],
                sample_count=30,
            ),
        ]
        candidates, _ = gk.filter(clusters, 10.0)
        summaries = [_summary(cluster_id=0), _summary(cluster_id=1)]

        results = describer.describe_batch(candidates, summaries)
        assert len(results) == 2
        assert results[0].knowledge_state == ScenarioKnowledgeState.DESCRIBED
        assert results[1].metadata.get("describe_fallback") is True
