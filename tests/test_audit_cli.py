"""CLI surfaces of the audit trail: operator identity (A8) and A4 path override."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from chaosgen.cli import _resolve_audit_actor_cli, main
from chaosgen.config.settings import load_settings


def _settings_file(tmp_path: Path) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text("llm_provider: ollama\n", encoding="utf-8")
    return path


class TestConfigSetOperator:
    def test_persists_operator_name(self, tmp_path: Path, monkeypatch):
        settings_file = tmp_path / "settings.yaml"
        monkeypatch.setattr("chaosgen.config.paths.SETTINGS_FILE", settings_file)
        monkeypatch.setattr("chaosgen.config.settings.SETTINGS_FILE", settings_file)

        result = CliRunner().invoke(main, ["config", "set-operator", " sre-anh "])

        assert result.exit_code == 0, result.output
        assert load_settings(str(settings_file)).operator_name == "sre-anh"

    def test_blank_name_rejected(self, tmp_path: Path, monkeypatch):
        settings_file = tmp_path / "settings.yaml"
        monkeypatch.setattr("chaosgen.config.settings.SETTINGS_FILE", settings_file)

        result = CliRunner().invoke(main, ["config", "set-operator", "   "])

        assert result.exit_code != 0
        assert "cannot be empty" in result.output


class TestResolveActorCli:
    def test_prompt_persists_and_returns(self, tmp_path: Path, monkeypatch):
        settings_file = _settings_file(tmp_path)
        monkeypatch.setattr("click.prompt", lambda *a, **k: "sre-anh")

        assert _resolve_audit_actor_cli(str(settings_file)) == "sre-anh"
        assert load_settings(str(settings_file)).operator_name == "sre-anh"

    def test_declined_prompt_stops_command(self, tmp_path: Path, monkeypatch):
        import click

        settings_file = _settings_file(tmp_path)
        monkeypatch.setattr("click.prompt", lambda *a, **k: "")

        try:
            _resolve_audit_actor_cli(str(settings_file))
        except click.ClickException as exc:
            assert "operator" in str(exc).lower()
        else:  # pragma: no cover - guard against silent default
            raise AssertionError("expected ClickException when actor is declined")


class TestRunCommandAuditContext:
    def test_approve_all_force_sets_path_override(self, monkeypatch):
        captured = {}

        class FakeExperiment:
            name = "checkout-latency"

        class FakeOrchestrator:
            def __init__(self, config_path=None):
                self.pending_experiments = [FakeExperiment()]

            def set_audit_context(self, **kwargs):
                captured.update(kwargs)

            def approve_and_run(self, index):
                captured["approved_index"] = index

        monkeypatch.setattr(
            "chaosgen.orchestrator.ChaosOrchestrator", FakeOrchestrator
        )
        monkeypatch.setattr(
            "chaosgen.cli._resolve_audit_actor_cli", lambda config=None: "sre-anh"
        )

        result = CliRunner().invoke(main, ["run", "--approve-all", "--force"])

        assert result.exit_code == 0, result.output
        assert captured["actor"] == "sre-anh"
        assert captured["path_used"] == "cli_approve_all_force"
        assert captured["approved_index"] == 0

    def test_interactive_approval_keeps_default_path(self, monkeypatch):
        captured = {}

        class FakeExperiment:
            name = "checkout-latency"

        class FakeOrchestrator:
            def __init__(self, config_path=None):
                self.pending_experiments = [FakeExperiment()]

            def set_audit_context(self, **kwargs):
                captured.update(kwargs)

            def approve_and_run(self, index):
                captured["approved_index"] = index

        monkeypatch.setattr(
            "chaosgen.orchestrator.ChaosOrchestrator", FakeOrchestrator
        )
        monkeypatch.setattr(
            "chaosgen.cli._resolve_audit_actor_cli", lambda config=None: "sre-anh"
        )

        result = CliRunner().invoke(main, ["run"], input="n\n")

        assert result.exit_code == 0, result.output
        assert captured["path_used"] is None
        assert "approved_index" not in captured
