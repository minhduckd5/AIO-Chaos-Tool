"""Integration tests for P4 — shared advisor pipeline and CLI wiring."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pydantic import BaseModel

from chaosgen.advisor.llm_advisor import LLMAdvisor, LLMProvider
from chaosgen.advisor.pipeline import (
    filter_chaos_descriptions,
    run_advisor_pipeline,
    summaries_for_chaos,
)
from chaosgen.advisor.scenario_describer import ScenarioDescriber
from chaosgen.cli import main
from chaosgen.config.settings import ChaosGenSettings
from chaosgen.schemas.faults import ChaosExperiment, FaultType, TargetSpec, TargetType
from chaosgen.schemas.scenarios import (
    AdvisorReport,
    AnomalyCluster,
    AnomalySeverity,
    AnomalySummary,
    FaultHypothesis,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)


class FakeProvider(LLMProvider):
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


def _good_describe_payload(cluster_id=0, service="payments"):
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
        "source_incident_id": cluster_id,
    }


def _real_cluster(cluster_id=0, service="payments"):
    return AnomalyCluster(
        cluster_id=cluster_id,
        severity=AnomalySeverity.HIGH,
        affected_services=[service],
        dominant_features=[("http_latency__payments__p99", 3.2)],
        sample_count=30,
    )


def _summary(cluster_id=0, service="payments"):
    return AnomalySummary(
        service_name=service,
        top_features=[("http_latency__payments__p99", 3.2)],
        error_pattern="error: timeout",
        severity=0.75,
        time_window="2024-01-01 00:00 - 00:15 UTC",
        source_cluster_id=cluster_id,
    )


def _noise_cluster():
    return AnomalyCluster(
        cluster_id=99,
        severity=AnomalySeverity.LOW,
        affected_services=["svc-a"],
        dominant_features=[("cpu_usage__svc-a__mean", 1.1)],
        sample_count=2,
    )


def _hypothesis(cluster_id=0):
    return FaultHypothesis(
        fault_type=FaultType.NETWORK_LATENCY,
        target_hint="payments",
        rationale="Latency spike matches network partition pattern",
        confidence=0.85,
        source_cluster_id=cluster_id,
    )


class TestDownstreamFilter:
    def test_filter_chaos_descriptions_rejects_fallback(self):
        good = UnknownScenarioDescription(**_good_describe_payload())
        fallback = UnknownScenarioDescription(
            title="Undescribed incident — cluster 1",
            root_cause_hypothesis="Telemetry anomaly on payments: timeout; verdict=real",
            repro_steps=["Inspect metrics", "Correlate deployment"],
            blast_radius_estimate="Primary: payments",
            suggested_fault_type=FaultType.NETWORK_LATENCY,
            confidence=0.0,
            source_incident_id=1,
            knowledge_state=ScenarioKnowledgeState.UNKNOWN,
            metadata={"describe_fallback": True},
        )
        result = filter_chaos_descriptions([good, fallback])
        assert len(result) == 1
        assert result[0].source_incident_id == 0

    def test_summaries_for_chaos_restricts_cluster_ids(self):
        desc = UnknownScenarioDescription(**_good_describe_payload(0))
        summaries = [_summary(0), _summary(1, "orders")]
        out = summaries_for_chaos(summaries, [desc])
        assert len(out) == 1
        assert out[0].source_cluster_id == 0


class TestRunAdvisorPipeline:
    def test_noise_filtered_no_experiments(self):
        clusters = [_noise_cluster()]
        summaries = [
            AnomalySummary(
                service_name="svc-a",
                top_features=[],
                severity=0.2,
                time_window="t",
                source_cluster_id=99,
            )
        ]
        interpret = LLMAdvisor(provider=FakeProvider([]))
        report = run_advisor_pipeline(
            clusters,
            summaries,
            settings=ChaosGenSettings(),
            lookback_hours=10.0,
            generate_chaos=True,
            describer=ScenarioDescriber(provider=FakeProvider([])),
            llm_advisor=interpret,
        )
        assert report.filtered_noise_count == 1
        assert report.descriptions == []
        assert report.generated_experiments == []

    def test_describe_fallback_produces_zero_experiments(self):
        clusters = [_real_cluster()]
        summaries = [_summary()]
        bad_then_exhaust = [
            {"repro_steps": ["only one"]},
            {"repro_steps": ["only one"]},
        ]
        describer = ScenarioDescriber(provider=FakeProvider(bad_then_exhaust), max_retries=2)
        interpret_provider = FakeProvider([_hypothesis()])
        interpret = LLMAdvisor(provider=interpret_provider)

        report = run_advisor_pipeline(
            clusters,
            summaries,
            settings=ChaosGenSettings(),
            lookback_hours=10.0,
            describer=describer,
            llm_advisor=interpret,
        )
        assert len(report.descriptions) == 1
        assert report.descriptions[0].metadata.get("describe_fallback")
        assert report.generated_experiments == []
        assert interpret_provider.calls == 0

    def test_full_path_produces_experiments(self):
        clusters = [_real_cluster()]
        summaries = [_summary()]
        describer = ScenarioDescriber(provider=FakeProvider([_good_describe_payload()]))
        interpret = LLMAdvisor(provider=FakeProvider([_hypothesis()]))

        report = run_advisor_pipeline(
            clusters,
            summaries,
            settings=ChaosGenSettings(),
            lookback_hours=10.0,
            describer=describer,
            llm_advisor=interpret,
        )
        assert report.descriptions[0].knowledge_state == ScenarioKnowledgeState.DESCRIBED
        assert len(report.generated_experiments) >= 1

    def test_skip_gatekeeper_bypasses_noise_drop(self):
        clusters = [_noise_cluster()]
        summaries = [
            AnomalySummary(
                service_name="svc-a",
                top_features=[],
                severity=0.2,
                time_window="t",
                source_cluster_id=99,
            )
        ]
        describer = ScenarioDescriber(
            provider=FakeProvider([_good_describe_payload(99, "svc-a")])
        )
        interpret = LLMAdvisor(provider=FakeProvider([
            FaultHypothesis(
                fault_type=FaultType.RESOURCE_EXHAUSTION,
                target_hint="svc-a",
                rationale="CPU stress reproduces observed anomaly",
                confidence=0.7,
                source_cluster_id=99,
            )
        ]))

        report = run_advisor_pipeline(
            clusters,
            summaries,
            settings=ChaosGenSettings(),
            lookback_hours=10.0,
            skip_gatekeeper=True,
            describer=describer,
            llm_advisor=interpret,
        )
        assert report.filtered_noise_count == 0
        assert report.generated_experiments


class TestCliIncidentsPromote:
    def _sample_report_path(self, tmp_path: Path) -> Path:
        desc = UnknownScenarioDescription(**_good_describe_payload())
        exp = ChaosExperiment(
            name="ai-network_latency-payments",
            description="latency test",
            target=TargetSpec(type=TargetType.SERVICE, name="payments"),
            faults=[],
        )
        report = AdvisorReport(
            anomalies_found=1,
            descriptions=[desc],
            generated_experiments=[exp],
        )
        path = tmp_path / "report.json"
        path.write_text(json.dumps(report.model_dump(mode="json")), encoding="utf-8")
        return path

    def test_incidents_from_report(self, tmp_path):
        path = self._sample_report_path(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["incidents", "--from-report", str(path), "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["knowledge_state"] == "described"

    def test_promote_requires_criteria_file(self, tmp_path):
        path = self._sample_report_path(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "promote",
                "--from-report", str(path),
                "--approved-by", "operator",
                "--experiment", "0",
            ],
        )
        assert result.exit_code != 0

    def test_promote_blocks_fallback(self, tmp_path):
        fallback = UnknownScenarioDescription(
            title="Undescribed incident — cluster 0",
            root_cause_hypothesis="Telemetry anomaly on payments: timeout; verdict=real",
            repro_steps=["Inspect metrics", "Correlate deployment"],
            blast_radius_estimate="Primary: payments",
            suggested_fault_type=FaultType.NETWORK_LATENCY,
            confidence=0.0,
            source_incident_id=0,
            knowledge_state=ScenarioKnowledgeState.UNKNOWN,
            metadata={"describe_fallback": True},
        )
        exp = ChaosExperiment(
            name="ai-network_latency-payments",
            description="latency test",
            target=TargetSpec(type=TargetType.SERVICE, name="payments"),
            faults=[],
        )
        report = AdvisorReport(
            anomalies_found=1,
            descriptions=[fallback],
            generated_experiments=[exp],
        )
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(report.model_dump(mode="json")), encoding="utf-8")
        criteria_path = tmp_path / "criteria.yaml"
        criteria_path.write_text(
            yaml.dump({"http_health": "https://payments/health"}),
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "promote",
                "--from-report", str(report_path),
                "--approved-by", "operator",
                "--experiment", "0",
                "--criteria-file", str(criteria_path),
                "--verdict", "pass",
            ],
        )
        assert result.exit_code != 0
        assert "fallback" in result.output.lower() or "described" in result.output.lower()
