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

    def test_export_analysis_generates_timeline_plot(self):
        req = AnalysisRequest(
            source="export",
            prom_url="http://127.0.0.1:9090",
            loki_url="http://127.0.0.1:3100",
            export_path=str(FIXTURE),
            generate_scenarios=False,
        )
        result = run_analysis(req)
        assert result.plot_path is not None
        assert Path(result.plot_path).exists()
        
        # Verify timeline dataframe is returned
        assert result.timeline_df is not None
        assert not result.timeline_df.empty
        assert "score" in result.timeline_df.columns
        assert "anomaly_cluster" in result.timeline_df.columns

        # Clean up generated plot if exists
        if Path(result.plot_path).exists():
            Path(result.plot_path).unlink()
