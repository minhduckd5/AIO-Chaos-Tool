"""Additional CLI coverage for P6 G6.1."""
from __future__ import annotations

from click.testing import CliRunner

from chaosgen.cli import main


class TestCliEvaluateAndStatus:
    def test_evaluate_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["evaluate", "--help"])
        assert result.exit_code == 0
        assert "KPI" in result.output or "evaluate" in result.output.lower()

    def test_status_lists_modules(self):
        runner = CliRunner()
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "Orchestrator" in result.output or "Module" in result.output

    def test_generate_from_catalog(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", "--from-catalog", "--arch", "microservices", "--top-n", "2"],
        )
        assert result.exit_code == 0
        assert "upstream" in result.output.lower() or "scenario" in result.output.lower()

    def test_train_model_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["train-model", "--help"])
        assert result.exit_code == 0
        assert "train-model" in result.output.lower() or "serialize" in result.output.lower()

    def test_train_model_command_runs(self):
        from unittest.mock import MagicMock, patch
        runner = CliRunner()
        
        with patch("chaosgen.ingestion.export_loader.ExportLoader.resolve_bundle") as mock_resolve, \
             patch("chaosgen.ml.feature_engineering.FeatureEngineer.transform") as mock_transform, \
             patch("chaosgen.ml.anomaly_detector.AnomalyDetector.fit") as mock_fit, \
             patch("chaosgen.ml.anomaly_detector.AnomalyDetector.detect") as mock_detect, \
             patch("chaosgen.ml.anomaly_detector.AnomalyDetector.save_model") as mock_save:
             
             mock_dataset = MagicMock()
             mock_dataset.metrics = [1]
             mock_dataset.total_samples = 100
             mock_dataset.logs = [1]
             
             mock_loader = MagicMock()
             mock_loader.load.return_value = mock_dataset
             mock_resolve.return_value = mock_loader
             
             import pandas as pd
             mock_df = pd.DataFrame({"f1": [1, 2]})
             mock_transform.return_value = mock_df
             mock_detect.return_value = []
             mock_fit.return_value = MagicMock(last_chosen_k=0)
             
             result = runner.invoke(main, ["train-model", "--export", ".", "--output-model", "test_model.joblib"])
             
             assert result.exit_code == 0
             assert "Model training and serialization completed successfully." in result.output
             assert mock_fit.called
             assert mock_save.called


class TestCliWindowFlags:
    def test_analyze_hours_xor_start_end(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "analyze",
                "--hours", "24",
                "--start", "2026-07-20T08:00:00Z",
                "--end", "2026-07-21T08:00:00Z",
            ],
        )
        assert result.exit_code != 0
        assert "either --hours" in result.output.lower() or "not both" in result.output.lower()

    def test_generate_help_lists_window_flags(self):
        runner = CliRunner()
        result = runner.invoke(main, ["generate", "--help"])
        assert result.exit_code == 0
        assert "--hours" in result.output
        assert "--start" in result.output
        assert "--export" in result.output
