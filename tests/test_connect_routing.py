"""Tests for connect profile routing (WS-3)."""

from chaosgen.config.connect_routing import (
    apply_connect_profile_to_orchestrator,
    execution_environment_from_settings,
    module_connect_configs,
    sync_inject_from_connect,
)
from chaosgen.config.settings import (
    ChaosGenSettings,
    ConnectSettings,
    DockerConnectSettings,
    KubernetesConnectSettings,
    UserHints,
)
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType
from chaosgen.ucal.translator import ExecutionEnvironment


def test_docker_compose_maps_to_docker_execution_env():
    settings = ChaosGenSettings(
        hints=UserHints(
            architecture=ArchitectureType.MODULAR_MONOLITH,
            environment=EnvironmentType.DOCKER_COMPOSE,
            skip_auto_detect=True,
        ),
    )
    assert execution_environment_from_settings(settings) == ExecutionEnvironment.DOCKER


def test_sync_inject_from_connect_kubeconfig():
    settings = ChaosGenSettings(
        connect=ConnectSettings(
            kubernetes=KubernetesConnectSettings(kubeconfig="/tmp/k3s.yaml", context="k3s"),
        ),
    )
    sync_inject_from_connect(settings)
    assert settings.inject.kubeconfig == "/tmp/k3s.yaml"
    assert settings.inject.context == "k3s"
    assert settings.inject.enabled is True


def test_module_connect_configs_includes_pumba_docker():
    settings = ChaosGenSettings(
        hints=UserHints(
            architecture=ArchitectureType.MODULAR_MONOLITH,
            environment=EnvironmentType.DOCKER_COMPOSE,
        ),
        connect=ConnectSettings(
            docker=DockerConnectSettings(
                host="unix:///var/run/docker.sock",
                compose_file="labs/modular-monolith/docker-compose.yml",
            ),
        ),
    )
    cfgs = module_connect_configs(settings)
    assert cfgs["pumba"]["docker_host"] == "unix:///var/run/docker.sock"
    assert "modular-monolith" in cfgs["pumba"]["compose_file"]


class _FakeOrchestrator:
    def __init__(self, settings: ChaosGenSettings):
        self._cg_settings = settings
        self.modules = {"pumba": _FakeModule(), "kubectl-chaos": _FakeModule()}
        from chaosgen.ucal.translator import ChaosTranslator

        self.translator = ChaosTranslator(inject_settings=settings.inject)


class _FakeModule:
    def __init__(self):
        self.config = {}


def test_apply_connect_profile_sets_translator_env():
    settings = ChaosGenSettings(
        hints=UserHints(
            architecture=ArchitectureType.MODULAR_MONOLITH,
            environment=EnvironmentType.DOCKER_COMPOSE,
            skip_auto_detect=True,
        ),
        connect=ConnectSettings(
            docker=DockerConnectSettings(host="unix:///var/run/docker.sock"),
        ),
    )
    orch = _FakeOrchestrator(settings)
    apply_connect_profile_to_orchestrator(orch)
    assert orch.translator.env == ExecutionEnvironment.DOCKER
    assert orch.modules["pumba"].config.get("docker_host") == "unix:///var/run/docker.sock"
