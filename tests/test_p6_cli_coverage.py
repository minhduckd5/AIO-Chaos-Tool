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
