"""P0-B ExpectationVerdictEngine tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from chaosgen.advisor.catalog_promoter import (
    evaluate_acceptance,
    validate_acceptance_criteria,
)
from chaosgen.evaluation.expectation_verdict import ExpectationVerdictEngine
from chaosgen.schemas.scenarios import ExperimentVerdict


class TestExpectationVerdictEngine:
    def test_empty_criteria_pass_with_guidance(self):
        engine = ExpectationVerdictEngine()
        report = engine.evaluate({})
        assert report.verdict == ExperimentVerdict.PASS
        assert "no expectations" in report.rationale.lower()

    def test_http_fail_required(self):
        engine = ExpectationVerdictEngine()
        with patch.object(engine._legacy, "_check_http", return_value=False):
            report = engine.evaluate(
                {
                    "claim": "Service stays up",
                    "expectations": [
                        {
                            "id": "http_alive",
                            "type": "http_health",
                            "url": "http://localhost/health",
                            "required": True,
                        }
                    ],
                },
                poll=False,
            )
        assert report.verdict == ExperimentVerdict.FAIL
        assert "FAIL" in report.rationale
        assert report.checks[0].passed is False

    def test_optional_fail_is_partial(self):
        engine = ExpectationVerdictEngine()
        with patch.object(engine._legacy, "_check_http", return_value=False):
            report = engine.evaluate(
                {
                    "claim": "Core SLA holds; health is optional",
                    "expectations": [
                        {
                            "id": "http_alive",
                            "type": "http_health",
                            "url": "http://localhost/health",
                            "required": False,
                        }
                    ],
                },
                poll=False,
            )
        assert report.verdict == ExperimentVerdict.PARTIAL

    def test_prometheus_threshold_pass(self):
        engine = ExpectationVerdictEngine(default_prometheus_url="http://prom:9090")
        with patch.object(engine, "_query_prometheus_scalar", return_value=3.0):
            report = engine.evaluate(
                {
                    "claim": "Ready replicas >= 2 within SLA",
                    "expectations": [
                        {
                            "id": "scale_up_sla",
                            "type": "prometheus_threshold",
                            "query": "sum(kube_deployment_status_replicas_ready)",
                            "op": ">=",
                            "threshold": 2,
                            "window_seconds": 0,
                        }
                    ],
                },
                poll=False,
            )
        assert report.verdict == ExperimentVerdict.PASS
        assert report.checks[0].observed == 3.0

    def test_prometheus_threshold_fail_rationale(self):
        engine = ExpectationVerdictEngine(default_prometheus_url="http://prom:9090")
        with patch.object(engine, "_query_prometheus_scalar", return_value=1.0):
            report = engine.evaluate(
                {
                    "claim": "Autoscaling restores capacity within 45s",
                    "expectations": [
                        {
                            "id": "scale_up_sla",
                            "type": "prometheus_threshold",
                            "query": "sum(ready)",
                            "op": ">=",
                            "threshold": 2,
                            "window_seconds": 0,
                        }
                    ],
                },
                poll=False,
            )
        assert report.verdict == ExperimentVerdict.FAIL
        assert "capacity/recovery claim not met" in report.checks[0].message
        assert "Autoscaling" in report.rationale

    def test_stakeholder_helpers_fail(self):
        from chaosgen.evaluation.expectation_verdict import (
            ExpectationCheckResult,
            ExpectationVerdictReport,
        )
        from chaosgen.schemas.scenarios import ExperimentVerdict

        report = ExpectationVerdictReport(
            claim="Autoscaling recovers within 45s",
            verdict=ExperimentVerdict.FAIL,
            rationale="Claim missed — scale too slow",
            checks=[
                ExpectationCheckResult(
                    id="scale_up_sla",
                    check_type="prometheus_threshold",
                    passed=False,
                    message="Observed 1 did not satisfy >= 2",
                )
            ],
        )
        assert "did not meet" in report.stakeholder_headline().lower()
        assert "IMPROVE" in report.stakeholder_status_label()
        notes = report.improvement_notes()
        assert notes
        assert any("autoscaling" in n.lower() or "capacity" in n.lower() for n in notes)


class TestCriteriaValidation:
    def test_expectations_schema(self):
        errs = validate_acceptance_criteria(
            {
                "claim": "x",
                "expectations": [
                    {"id": "a", "type": "prometheus_threshold", "query": "up", "op": ">=", "threshold": 1}
                ],
            }
        )
        assert errs == []

    def test_evaluate_acceptance_uses_engine(self):
        with patch.object(
            ExpectationVerdictEngine,
            "evaluate",
            return_value=MagicMock(verdict=ExperimentVerdict.FAIL),
        ):
            v = evaluate_acceptance(
                {
                    "claim": "c",
                    "expectations": [
                        {"id": "h", "type": "http_health", "url": "http://x"}
                    ],
                },
                poll=False,
            )
            assert v == ExperimentVerdict.FAIL
