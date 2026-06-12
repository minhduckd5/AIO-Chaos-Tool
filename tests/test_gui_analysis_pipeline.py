"""Tests for GUI analysis pipeline."""

from pathlib import Path

from chaosgen.gui.analysis_pipeline import AnalysisRequest, run_analysis

FIXTURE = Path(__file__).parent / "fixtures" / "export_bundle"


class TestGuiAnalysisPipeline:
    def test_export_analysis_without_llm(self):
        req = AnalysisRequest(
            source="export",
            prom_url="http://127.0.0.1:9090",
            loki_url="http://127.0.0.1:3100",
            export_path=str(FIXTURE),
            generate_scenarios=False,
        )
        result = run_analysis(req)
        assert result.metric_series >= 1
        assert result.source_label.startswith("export:")
