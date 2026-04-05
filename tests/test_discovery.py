"""
Tests for the discovery engine — environment probe, architecture classifier,
service mapper, observability probe, and the run_full_discovery() integration.

All external calls (K8s API, Docker socket, HTTP endpoints) are mocked so
tests run in a clean environment without any live infrastructure.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaosgen.schemas.discovery import (
    ArchitectureType,
    DiscoveryReport,
    EnvironmentType,
)


# ---------------------------------------------------------------------------
# EnvironmentProbe
# ---------------------------------------------------------------------------


class TestEnvironmentProbe:
    def test_kubernetes_detected_via_kubeconfig(self, tmp_path):
        """K8s detected when kubeconfig file exists."""
        from chaosgen.discovery.environment_probe import EnvironmentProbe

        kube = tmp_path / "config"
        kube.write_text("apiVersion: v1\n")

        probe = EnvironmentProbe(kubeconfig=str(kube))

        with patch("chaosgen.discovery.environment_probe.EnvironmentProbe._build_kubernetes_profile") as mock_build:
            from chaosgen.schemas.discovery import EnvironmentProfile
            mock_build.return_value = EnvironmentProfile(type=EnvironmentType.KUBERNETES, node_count=3)
            result = probe.probe()

        assert result.type == EnvironmentType.KUBERNETES

    def test_docker_detected_via_compose_file(self, tmp_path, monkeypatch):
        """Docker environment detected when docker-compose.yml present in cwd."""
        from chaosgen.discovery.environment_probe import EnvironmentProbe

        monkeypatch.chdir(tmp_path)
        (tmp_path / "docker-compose.yml").write_text("services:\n  app:\n    image: nginx\n")

        probe = EnvironmentProbe(kubeconfig=str(tmp_path / "no_kube_config"))
        with patch("chaosgen.discovery.environment_probe.EnvironmentProbe._detect_cloud_vm", return_value=None):
            result = probe.probe()

        assert result.type == EnvironmentType.DOCKER_COMPOSE

    def test_bare_metal_fallback(self, tmp_path, monkeypatch):
        """Falls back to bare metal when no environment signals are detected."""
        from chaosgen.discovery.environment_probe import EnvironmentProbe

        monkeypatch.chdir(tmp_path)  # no compose file here
        probe = EnvironmentProbe(kubeconfig=str(tmp_path / "no_config"))

        with patch("chaosgen.discovery.environment_probe.EnvironmentProbe._detect_cloud_vm", return_value=None):
            result = probe.probe()

        assert result.type == EnvironmentType.BARE_METAL


# ---------------------------------------------------------------------------
# ArchitectureClassifier
# ---------------------------------------------------------------------------


class TestArchitectureClassifier:
    def _make_docker_compose(self, tmp_path: object, services: dict) -> str:
        import yaml
        path = tmp_path / "docker-compose.yml"
        path.write_text(yaml.dump({"services": services}))
        return str(path)

    def test_monolith_single_service(self, tmp_path):
        from chaosgen.discovery.architecture_classifier import ArchitectureClassifier
        from chaosgen.schemas.discovery import EnvironmentProfile

        compose = self._make_docker_compose(tmp_path, {"app": {"image": "myapp"}})
        env = EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE)
        classifier = ArchitectureClassifier(env_profile=env, compose_file=compose)
        result = classifier.classify()
        assert result.type == ArchitectureType.MONOLITH
        assert result.service_count == 1

    def test_microservices_many_services(self, tmp_path):
        from chaosgen.discovery.architecture_classifier import ArchitectureClassifier
        from chaosgen.schemas.discovery import EnvironmentProfile

        services = {f"svc{i}": {"image": f"svc{i}"} for i in range(6)}
        compose = self._make_docker_compose(tmp_path, services)
        env = EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE)
        classifier = ArchitectureClassifier(env_profile=env, compose_file=compose)
        result = classifier.classify()
        assert result.type == ArchitectureType.MICROSERVICES

    def test_event_driven_kafka(self, tmp_path):
        from chaosgen.discovery.architecture_classifier import ArchitectureClassifier
        from chaosgen.schemas.discovery import EnvironmentProfile

        services = {
            "api":   {"image": "api"},
            "kafka": {"image": "confluentinc/cp-kafka:7.5"},
            "worker": {"image": "worker"},
        }
        compose = self._make_docker_compose(tmp_path, services)
        env = EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE)
        classifier = ArchitectureClassifier(env_profile=env, compose_file=compose)
        result = classifier.classify()
        assert result.type == ArchitectureType.EVENT_DRIVEN
        assert result.has_message_broker is True

    def test_client_server_two_services(self, tmp_path):
        from chaosgen.discovery.architecture_classifier import ArchitectureClassifier
        from chaosgen.schemas.discovery import EnvironmentProfile

        services = {"client": {"image": "client"}, "server": {"image": "server"}}
        compose = self._make_docker_compose(tmp_path, services)
        env = EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE)
        classifier = ArchitectureClassifier(env_profile=env, compose_file=compose)
        result = classifier.classify()
        assert result.type == ArchitectureType.CLIENT_SERVER


# ---------------------------------------------------------------------------
# ObservabilityProbe
# ---------------------------------------------------------------------------


class TestObservabilityProbe:
    def test_all_tools_detected(self):
        from chaosgen.discovery.observability_probe import ObservabilityProbe

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "data": {"result": []}}

        with patch("chaosgen.discovery.observability_probe.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            MockSession.return_value = mock_session

            probe = ObservabilityProbe()
            result, signals = probe.probe()

        assert result.has_metrics is True
        assert result.has_logs is True
        assert result.has_traces is True

    def test_no_tools_detected(self):
        from chaosgen.discovery.observability_probe import ObservabilityProbe
        import requests as req

        with patch("chaosgen.discovery.observability_probe.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.side_effect = req.ConnectionError
            MockSession.return_value = mock_session

            probe = ObservabilityProbe()
            result, signals = probe.probe()

        assert result.has_metrics is False
        assert result.has_logs is False
        assert result.telemetry_ready is False


# ---------------------------------------------------------------------------
# run_full_discovery integration
# ---------------------------------------------------------------------------


class TestRunFullDiscovery:
    def test_returns_discovery_report(self, tmp_path, monkeypatch):
        """Integration test — returns DiscoveryReport even when probes fail gracefully."""
        from chaosgen.discovery import run_full_discovery
        import requests as req

        monkeypatch.chdir(tmp_path)

        with patch("chaosgen.discovery.observability_probe.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.side_effect = req.ConnectionError
            MockSession.return_value = mock_session

            report = run_full_discovery()

        assert isinstance(report, DiscoveryReport)
        assert report.environment.type in EnvironmentType.__members__.values()
        assert hasattr(report, "signals")
