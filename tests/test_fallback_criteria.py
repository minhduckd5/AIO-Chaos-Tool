"""CTK evaluation fallback criteria path resolution (lab vs production-scale)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from chaosgen.config.settings import (
    ChaosGenSettings,
    EvaluationSettings,
    ObservabilityHint,
    UserHints,
)
from chaosgen.evaluation.fallback_criteria import (
    LAB_BOUTIQUE_CRITERIA_REL,
    PRODUCTION_DEMO_CRITERIA_REL,
    load_fallback_acceptance_criteria,
    resolve_fallback_criteria_path,
)
from chaosgen.schemas.discovery import ObservabilityTool


def _settings(
    *,
    prom_url: str | None = None,
    fallback_path: str | None = None,
) -> ChaosGenSettings:
    obs: list[ObservabilityHint] = []
    if prom_url:
        obs.append(
            ObservabilityHint(tool=ObservabilityTool.PROMETHEUS, url=prom_url)
        )
    return ChaosGenSettings(
        hints=UserHints(observability=obs),
        evaluation=EvaluationSettings(fallback_criteria_path=fallback_path),
    )


class TestResolveFallbackCriteriaPath:
    def test_explicit_setting_wins_over_lab_url(self, tmp_path: Path):
        explicit = tmp_path / "custom.yaml"
        explicit.write_text("claim: custom\nexpectations: []\n", encoding="utf-8")
        settings = _settings(
            prom_url="http://10.50.1.220:9090",
            fallback_path=str(explicit),
        )
        assert resolve_fallback_criteria_path(settings) == explicit.resolve()

    def test_lab_prometheus_selects_boutique_criteria(self):
        settings = _settings(prom_url="http://10.50.1.220:9090")
        path = resolve_fallback_criteria_path(settings)
        assert path is not None
        assert path.as_posix().endswith(LAB_BOUTIQUE_CRITERIA_REL)
        assert path.is_file()

    def test_non_lab_prometheus_selects_production_demo_criteria(self):
        settings = _settings(prom_url="http://127.0.0.1:9090")
        path = resolve_fallback_criteria_path(settings)
        assert path is not None
        assert path.as_posix().endswith(PRODUCTION_DEMO_CRITERIA_REL)
        assert path.is_file()

    def test_lab_file_threshold_is_one_not_two(self):
        settings = _settings(prom_url="http://10.50.1.220:9090")
        criteria = load_fallback_acceptance_criteria(settings)
        assert criteria is not None
        expectations = criteria.get("expectations") or []
        thr = {
            e["id"]: e.get("threshold")
            for e in expectations
            if isinstance(e, dict) and "threshold" in e
        }
        assert "capacity_hold_sla" in thr
        assert thr["capacity_hold_sla"] == 1
        assert 2 not in thr.values()


class TestEvaluateCtkRunUsesLabFallback:
    def test_evaluate_ctk_run_loads_lab_criteria_when_prom_is_lab(self):
        from chaosgen.orchestrator import ChaosOrchestrator

        orch = ChaosOrchestrator.__new__(ChaosOrchestrator)
        orch.logger = MagicMock()

        captured: dict = {}

        def _fake_build(**kwargs):
            captured["acceptance_criteria"] = kwargs.get("acceptance_criteria")
            report = MagicMock()
            report.verdict = MagicMock(value="pass")
            return report

        settings = _settings(prom_url="http://10.50.1.220:9090")
        with (
            patch(
                "chaosgen.config.telemetry_endpoints.resolve_prometheus_url",
                return_value="http://10.50.1.220:9090",
            ),
            patch(
                "chaosgen.config.settings.load_settings",
                return_value=settings,
            ),
            patch(
                "chaosgen.evaluation.ctk_verdict.build_verdict_from_ctk_run",
                side_effect=lambda *a, **k: _fake_build(**k),
            ),
            patch(
                "chaosgen.advisor.report_store.save_verdict_report",
                return_value=Path("scratch/verdict.json"),
            ),
        ):
            orch._evaluate_ctk_run(
                {"success": True, "journal_path": "x.json", "dry_run": True},
                title="catalog-sample",
                description="non-fail-redis catalog CTK",
            )

        criteria = captured.get("acceptance_criteria") or {}
        assert criteria, "fallback criteria must be loaded"
        # Must not be production-scale threshold=2 SLA
        blob = yaml.safe_dump(criteria)
        assert "threshold: 2" not in blob
        assert "capacity_hold_sla" in blob or "threshold: 1" in blob
