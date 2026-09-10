"""Chaos Toolkit adapter — the canonical inject executor (ADR: CTK runtime).

Everything here mocks the subprocess boundary: no `chaos` CLI, no cluster.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from chaosgen.modules import chaos_toolkit as ctk
from chaosgen.modules.chaos_toolkit import ChaosToolkitModule, resolve_chaos_binary


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakePopen:
    """Popen stub: poll() returns None until `polls_until_exit` is exhausted."""

    def __init__(
        self,
        *,
        returncode=0,
        stdout="run ok",
        stderr="",
        polls_until_exit=1,
        on_poll=None,
        communicate_timeout=False,
    ):
        self.pid = 4242
        self.returncode = None
        self._final_returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._polls = polls_until_exit
        self._on_poll = on_poll
        self._communicate_timeout = communicate_timeout
        self.communicate_calls = 0

    def poll(self):
        if self._on_poll is not None:
            self._on_poll()
        if self._polls <= 0:
            self.returncode = self._final_returncode
            return self._final_returncode
        self._polls -= 1
        return None

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        if self._communicate_timeout and self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(cmd="chaos", timeout=timeout or 1)
        self.returncode = self._final_returncode
        return self._stdout, self._stderr

    def wait(self, timeout=None):
        self.returncode = self._final_returncode
        return self._final_returncode


@pytest.fixture
def module(tmp_path: Path) -> ChaosToolkitModule:
    return ChaosToolkitModule(
        {
            "chaos_bin": "chaos",
            "timeout_s": 30,
            "journal_dir": str(tmp_path / "journals"),
        }
    )


@pytest.fixture
def experiment_file(tmp_path: Path) -> Path:
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps({"title": "lab"}), encoding="utf-8")
    return path


class TestBinaryResolution:
    def test_path_lookup_wins(self, monkeypatch):
        monkeypatch.setattr(ctk.shutil, "which", lambda name: "/usr/bin/chaos")
        assert resolve_chaos_binary() == "/usr/bin/chaos"

    def test_returns_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(ctk.shutil, "which", lambda name: None)
        monkeypatch.setattr(ctk.Path, "is_file", lambda self: False)
        assert resolve_chaos_binary() is None

    def test_missing_binary_is_reported_not_raised(self, monkeypatch, experiment_file):
        monkeypatch.setattr(ctk.shutil, "which", lambda name: None)
        monkeypatch.setattr(ctk, "resolve_chaos_binary", lambda: None)
        module = ChaosToolkitModule({"chaos_bin": None})

        result = module.execute(
            "validate", {"experiment_file": str(experiment_file)}
        )
        assert result["success"] is False
        assert "chaos CLI not found" in result["error"]

    def test_status_reports_configuration(self, module: ChaosToolkitModule):
        status = module.get_status()
        assert status["module"] == "chaos-toolkit"
        assert status["configured"] is True
        assert status["run_active"] is False
        assert module.get_available_actions() == [
            "run_experiment",
            "validate",
            "discover",
            "abort_run",
        ]
        assert module.validate_config() is True


class TestExecuteDispatch:
    def test_unknown_action(self, module: ChaosToolkitModule):
        result = module.execute("nuke_everything", {})
        assert result["success"] is False
        assert "Unknown action" in result["error"]

    def test_validate_missing_file(self, module: ChaosToolkitModule, tmp_path: Path):
        result = module.execute(
            "validate", {"experiment_file": str(tmp_path / "absent.json")}
        )
        assert result["success"] is False
        assert "experiment file not found" in result["error"]

    def test_run_experiment_missing_file(self, module: ChaosToolkitModule):
        result = module.execute("run_experiment", {"experiment_file": ""})
        assert result["success"] is False
        assert "experiment file not found" in result["error"]


class TestRunCmd:
    def test_validate_success(self, module, experiment_file, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["env"] = kwargs.get("env", {})
            return _Completed(0, stdout="experiment syntax is valid")

        monkeypatch.setattr(ctk.subprocess, "run", fake_run)
        result = module.execute("validate", {"experiment_file": str(experiment_file)})

        assert result["success"] is True
        assert result["action"] == "validate"
        assert seen["cmd"][:2] == ["chaos", "validate"]
        assert seen["env"]["PYTHONUTF8"] == "1"

    def test_failure_surfaces_stderr(self, module, experiment_file, monkeypatch):
        monkeypatch.setattr(
            ctk.subprocess,
            "run",
            lambda cmd, **kwargs: _Completed(1, stderr="invalid probe"),
        )
        result = module.execute("validate", {"experiment_file": str(experiment_file)})

        assert result["success"] is False
        assert result["error"] == "invalid probe"

    def test_windows_console_encoding_crash_is_not_a_failed_experiment(
        self, module, experiment_file, monkeypatch
    ):
        """CTK exits non-zero when the console cannot print its summary."""
        monkeypatch.setattr(
            ctk.subprocess,
            "run",
            lambda cmd, **kwargs: _Completed(
                1,
                stdout="Experiment ended with status: completed",
                stderr="UnicodeEncodeError: 'charmap' codec can't encode",
            ),
        )
        result = module.execute("validate", {"experiment_file": str(experiment_file)})

        assert result["success"] is True
        assert result["error"] is None

    def test_timeout_is_flagged(self, module, experiment_file, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=60, output="partial")

        monkeypatch.setattr(ctk.subprocess, "run", fake_run)
        result = module.execute("validate", {"experiment_file": str(experiment_file)})

        assert result["success"] is False
        assert result["timeout"] is True
        assert result["stdout"] == "partial"

    def test_binary_not_executable(self, module, experiment_file, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError(2, "not executable")

        monkeypatch.setattr(ctk.subprocess, "run", fake_run)
        result = module.execute("validate", {"experiment_file": str(experiment_file)})

        assert result["success"] is False
        assert "not executable" in result["error"]

    def test_discover_uses_no_install(self, module, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return _Completed(0, stdout="{}")

        monkeypatch.setattr(ctk.subprocess, "run", fake_run)
        result = module.execute("discover", {"package": "chaostoolkit-kubernetes"})

        assert result["action"] == "discover"
        assert "--no-install" in seen["cmd"]


class TestRunExperiment:
    def test_dry_run_only_validates(self, module, experiment_file, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return _Completed(0, stdout="valid")

        monkeypatch.setattr(ctk.subprocess, "run", fake_run)
        monkeypatch.setattr(
            ctk.subprocess,
            "Popen",
            lambda *a, **k: pytest.fail("dry-run must not start chaos run"),
        )

        result = module.execute(
            "run_experiment", {"experiment_file": str(experiment_file), "dry_run": True}
        )

        assert result["dry_run"] is True
        assert result["action"] == "run_experiment"
        assert seen["cmd"][1] == "validate"

    def test_live_run_parses_journal(self, module, experiment_file, tmp_path, monkeypatch):
        journal_path = tmp_path / "journal.json"

        def fake_popen(cmd, env=None, **kwargs):
            journal_path.write_text(
                json.dumps({"status": "completed", "deviated": False}), encoding="utf-8"
            )
            return _FakePopen(returncode=0, stdout="all good")

        monkeypatch.setattr(ctk.subprocess, "Popen", fake_popen)

        result = module.execute(
            "run_experiment",
            {
                "experiment_file": str(experiment_file),
                "journal_path": str(journal_path),
            },
        )

        assert result["success"] is True
        assert result["dry_run"] is False
        assert result["journal_status"] == "completed"
        assert result["deviated"] is False

    def test_corrupt_journal_does_not_fail_the_run(
        self, module, experiment_file, tmp_path, monkeypatch
    ):
        journal_path = tmp_path / "journal.json"

        def fake_popen(cmd, env=None, **kwargs):
            journal_path.write_text("{not json", encoding="utf-8")
            return _FakePopen(returncode=0)

        monkeypatch.setattr(ctk.subprocess, "Popen", fake_popen)

        result = module.execute(
            "run_experiment",
            {
                "experiment_file": str(experiment_file),
                "journal_path": str(journal_path),
            },
        )

        assert result["success"] is True
        assert "journal_status" not in result

    def test_default_journal_dir_is_created(self, module, experiment_file, monkeypatch):
        monkeypatch.setattr(
            ctk.subprocess, "Popen", lambda cmd, env=None, **kwargs: _FakePopen()
        )
        result = module.execute(
            "run_experiment", {"experiment_file": str(experiment_file)}
        )
        assert Path(result["journal_path"]).parent.is_dir()

    def test_nonzero_exit_is_a_failed_run(self, module, experiment_file, monkeypatch):
        monkeypatch.setattr(
            ctk.subprocess,
            "Popen",
            lambda cmd, env=None, **kwargs: _FakePopen(
                returncode=1, stdout="", stderr="probe failed"
            ),
        )
        result = module.execute(
            "run_experiment", {"experiment_file": str(experiment_file)}
        )

        assert result["success"] is False
        assert result["error"] == "probe failed"

    def test_popen_missing_binary(self, module, experiment_file, monkeypatch):
        def fake_popen(cmd, env=None, **kwargs):
            raise FileNotFoundError(2, "missing")

        monkeypatch.setattr(ctk.subprocess, "Popen", fake_popen)
        result = module.execute(
            "run_experiment", {"experiment_file": str(experiment_file)}
        )

        assert result["success"] is False
        assert "not executable" in result["error"]


class TestHalt:
    def test_abort_without_active_run(self, module: ChaosToolkitModule):
        assert module.abort_run() is False
        action = module.execute("abort_run", {})
        assert action["success"] is True
        assert action["aborted"] is False

    def test_abort_kills_active_process(self, module: ChaosToolkitModule, monkeypatch):
        killed = []
        monkeypatch.setattr(ctk, "_kill_process_tree", killed.append)
        proc = _FakePopen(polls_until_exit=10)
        module._proc = proc

        assert module.is_run_active() is True
        assert module.abort_run() is True
        assert killed == [proc.pid]
        assert module._proc is None
        assert module.is_run_active() is False

    def test_halt_during_run_returns_aborted(
        self, module: ChaosToolkitModule, experiment_file, monkeypatch
    ):
        monkeypatch.setattr(ctk, "_kill_process_tree", lambda pid: None)

        def fake_popen(cmd, env=None, **kwargs):
            # Operator hits HALT while the run is in flight.
            return _FakePopen(
                polls_until_exit=3,
                on_poll=lambda: setattr(module, "_abort_requested", True),
            )

        monkeypatch.setattr(ctk.subprocess, "Popen", fake_popen)
        result = module.execute(
            "run_experiment", {"experiment_file": str(experiment_file)}
        )

        assert result["aborted"] is True
        assert result["success"] is False
        assert "HALT" in result["error"]

    def test_run_timeout_kills_and_reports(
        self, module: ChaosToolkitModule, experiment_file, monkeypatch
    ):
        monkeypatch.setattr(ctk, "_kill_process_tree", lambda pid: None)
        monkeypatch.setattr(
            ctk.subprocess,
            "Popen",
            lambda cmd, env=None, **kwargs: _FakePopen(polls_until_exit=10_000),
        )

        result = module.execute(
            "run_experiment",
            {"experiment_file": str(experiment_file), "timeout_s": 1},
        )

        assert result["timeout"] is True
        assert result["success"] is False

    def test_stuck_communicate_is_force_killed(
        self, module: ChaosToolkitModule, experiment_file, monkeypatch
    ):
        killed = []
        monkeypatch.setattr(ctk, "_kill_process_tree", killed.append)
        monkeypatch.setattr(
            ctk.subprocess,
            "Popen",
            lambda cmd, env=None, **kwargs: _FakePopen(communicate_timeout=True),
        )

        result = module.execute(
            "run_experiment", {"experiment_file": str(experiment_file)}
        )

        assert killed == [4242]
        assert result["success"] is True


class TestProcessTreeKill:
    def test_zero_pid_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(
            ctk.subprocess,
            "run",
            lambda *a, **k: pytest.fail("must not shell out for pid 0"),
        )
        ctk._kill_process_tree(0)

    def test_windows_uses_taskkill(self, monkeypatch):
        if os.name != "nt":
            pytest.skip("Windows-only process tree kill")
        seen = {}
        monkeypatch.setattr(
            ctk.subprocess, "run", lambda cmd, **kwargs: seen.setdefault("cmd", cmd)
        )
        ctk._kill_process_tree(1234)
        assert seen["cmd"][:2] == ["taskkill", "/F"]

    def test_popen_kwargs_match_platform(self, module: ChaosToolkitModule):
        kwargs = module._popen_kwargs()
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["text"] is True
        if os.name == "nt":
            assert "creationflags" in kwargs
        else:
            assert kwargs["start_new_session"] is True
