"""Tests for Pumba real execution (WS-4)."""

from unittest.mock import MagicMock, patch

import pytest

from chaosgen.modules.pumba import PumbaModule


def _completed(rc=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestPumbaModule:
    def test_dry_run_kill_no_subprocess_mutation(self):
        mod = PumbaModule({"dry_run": True, "docker_host": "unix:///var/run/docker.sock"})
        with patch("chaosgen.modules.pumba.subprocess.run") as run:
            result = mod.execute("kill_container", {"container": "monolith-app", "signal": "SIGKILL"})
        run.assert_not_called()
        assert result["success"] is True
        assert result["dry_run"] is True
        assert "docker kill" in result["command"]

    def test_kill_resolves_container_and_calls_docker(self):
        mod = PumbaModule({"dry_run": False, "docker_host": "unix:///var/run/docker.sock"})

        def fake_run(argv, **kwargs):
            if argv[:2] == ["docker", "info"]:
                return _completed(0)
            if argv[:2] == ["docker", "ps"]:
                return _completed(0, stdout="chaosgen-monolith_monolith-app_1\n")
            if argv[:2] == ["docker", "kill"]:
                return _completed(0)
            return _completed(1, stderr="unexpected")

        with patch("chaosgen.modules.pumba.subprocess.run", side_effect=fake_run):
            result = mod.execute("kill_container", {"container": "monolith-app", "signal": "SIGKILL"})

        assert result["success"] is True
        assert result["container"] == "chaosgen-monolith_monolith-app_1"
        assert "docker kill" in result["command"]

    def test_delay_network_uses_pumba_wrapper_when_no_binary(self):
        mod = PumbaModule({"dry_run": False})

        def fake_run(argv, **kwargs):
            if argv[:2] == ["docker", "info"]:
                return _completed(0)
            if argv[:2] == ["docker", "ps"]:
                return _completed(0, stdout="module-api\n")
            if argv[:3] == ["docker", "run", "--rm"]:
                return _completed(0)
            return _completed(1)

        with patch("chaosgen.modules.pumba.subprocess.run", side_effect=fake_run):
            with patch("chaosgen.modules.pumba.shutil.which", return_value=None):
                result = mod.execute(
                    "delay_network",
                    {"container": "module-api", "delay": "800ms", "duration": "60s"},
                )

        assert result["success"] is True
        assert "netem" in result["command"]
        assert "gaiaadm/pumba" in result["command"]

    def test_fails_when_docker_unreachable(self):
        mod = PumbaModule({"dry_run": False})
        with patch("chaosgen.modules.pumba.subprocess.run", side_effect=FileNotFoundError("docker")):
            result = mod.execute("kill_container", {"container": "monolith-app"})
        assert result["success"] is False
        assert "Docker" in result["error"]

    def test_no_container_match_returns_error(self):
        mod = PumbaModule({"dry_run": False})

        def fake_run(argv, **kwargs):
            if argv[:2] == ["docker", "info"]:
                return _completed(0)
            if argv[:2] == ["docker", "ps"]:
                return _completed(0, stdout="")
            return _completed(1)

        with patch("chaosgen.modules.pumba.subprocess.run", side_effect=fake_run):
            result = mod.execute("kill_container", {"container": "missing-app"})

        assert result["success"] is False
        assert "no running container" in result["error"].lower()

    def test_validate_config_dry_run_without_docker(self):
        mod = PumbaModule({"dry_run": True})
        assert mod.validate_config() is True
