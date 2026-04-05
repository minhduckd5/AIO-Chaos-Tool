"""
Tests for the Bootstrap module — specifically Blind Spot 3 (binary prerequisites).

Validates:
  - check_prerequisites raises PrerequisiteError with install URLs when a binary is missing
  - check_prerequisites succeeds silently when all binaries are present
  - ObservabilityInstaller.install() raises PrerequisiteError before shelling out
  - Tier 2 (Docker Compose) injection is idempotent
  - Tier 3 (script generation) creates expected files without network calls
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from chaosgen.bootstrap.exceptions import PrerequisiteError
from chaosgen.bootstrap.observability_installer import check_prerequisites, ObservabilityInstaller
from chaosgen.schemas.discovery import EnvironmentProfile, EnvironmentType, ObservabilityProfile


class TestCheckPrerequisites:
    def test_raises_when_binary_missing(self):
        with patch("chaosgen.bootstrap.observability_installer.shutil.which", return_value=None):
            with pytest.raises(PrerequisiteError) as exc_info:
                check_prerequisites(["helm"])
            assert "helm" in str(exc_info.value)
            assert "https://helm.sh" in str(exc_info.value)

    def test_passes_when_all_binaries_present(self):
        with patch("chaosgen.bootstrap.observability_installer.shutil.which", return_value="/usr/bin/helm"):
            check_prerequisites(["helm"])  # should not raise

    def test_lists_all_missing_binaries(self):
        with patch("chaosgen.bootstrap.observability_installer.shutil.which", return_value=None):
            with pytest.raises(PrerequisiteError) as exc_info:
                check_prerequisites(["helm", "kubectl"])
            error_msg = str(exc_info.value)
            assert "helm" in error_msg
            assert "kubectl" in error_msg


class TestObservabilityInstallerTier1K8s:
    def _make_installer(self, **obs_kwargs):
        return ObservabilityInstaller(
            env_profile=EnvironmentProfile(type=EnvironmentType.KUBERNETES),
            obs_profile=ObservabilityProfile(has_metrics=False, has_logs=False, **obs_kwargs),
        )

    def test_prerequisite_check_before_helm_call(self):
        """ObservabilityInstaller must call check_prerequisites BEFORE any subprocess.run."""
        installer = self._make_installer()
        with patch("chaosgen.bootstrap.observability_installer.shutil.which", return_value=None):
            with pytest.raises(PrerequisiteError):
                installer.install()

    def test_no_subprocess_on_missing_binary(self):
        """subprocess.run must NEVER be called when a binary is missing."""
        installer = self._make_installer()
        with patch("chaosgen.bootstrap.observability_installer.shutil.which", return_value=None):
            with patch("chaosgen.bootstrap.observability_installer.subprocess.run") as mock_run:
                try:
                    installer.install()
                except PrerequisiteError:
                    pass
                mock_run.assert_not_called()


class TestObservabilityInstallerTier2Docker:
    def test_injects_prometheus_into_compose(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text(yaml.dump({"services": {"app": {"image": "myapp"}}}))

        installer = ObservabilityInstaller(
            env_profile=EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE),
            obs_profile=ObservabilityProfile(has_metrics=False, has_logs=False),
        )
        with patch("chaosgen.bootstrap.observability_installer.shutil.which", return_value="/usr/bin/docker"):
            actions = installer.install()

        updated = yaml.safe_load(compose_path.read_text())
        assert "prometheus" in updated["services"]
        assert "grafana" in updated["services"]
        assert "loki" in updated["services"]
        assert any("Injected" in a for a in actions)

    def test_idempotent_when_tools_already_present(self, tmp_path, monkeypatch):
        """Does not add services that are already in the compose file."""
        monkeypatch.chdir(tmp_path)
        existing = {
            "services": {
                "prometheus": {"image": "prom/prometheus:latest"},
                "app": {"image": "myapp"},
            }
        }
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text(yaml.dump(existing))

        installer = ObservabilityInstaller(
            env_profile=EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE),
            obs_profile=ObservabilityProfile(has_metrics=True, has_logs=False),
        )
        with patch("chaosgen.bootstrap.observability_installer.shutil.which", return_value="/usr/bin/docker"):
            installer.install()

        updated = yaml.safe_load(compose_path.read_text())
        # prometheus must remain but not be duplicated
        assert len([k for k in updated["services"] if k == "prometheus"]) == 1


class TestObservabilityInstallerTier3Scripts:
    def test_generates_script_files(self, tmp_path):
        installer = ObservabilityInstaller(
            env_profile=EnvironmentProfile(type=EnvironmentType.BARE_METAL),
            obs_profile=ObservabilityProfile(has_metrics=False, has_logs=False),
            output_dir=str(tmp_path),
        )
        actions = installer.install()

        assert (tmp_path / "install_prometheus.sh").exists()
        assert (tmp_path / "install_loki.sh").exists()
        assert (tmp_path / "prometheus.yml").exists()
        assert any("install_prometheus.sh" in a for a in actions)

    def test_script_contents_are_valid_bash(self, tmp_path):
        installer = ObservabilityInstaller(
            env_profile=EnvironmentProfile(type=EnvironmentType.BARE_METAL),
            obs_profile=ObservabilityProfile(has_metrics=False, has_logs=False),
            output_dir=str(tmp_path),
        )
        installer.install()

        content = (tmp_path / "install_prometheus.sh").read_text()
        assert "#!/usr/bin/env bash" in content
        assert "set -euo pipefail" in content
        assert "prometheus" in content.lower()
