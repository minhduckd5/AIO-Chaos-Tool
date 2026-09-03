"""Tests for form-first profile connect validation (WS-2)."""

from chaosgen.config.profile_validation import (
    ProfileValidationError,
    require_valid_profile_connect,
    validate_profile_connect,
)
from chaosgen.config.settings import (
    BrokerConnectSettings,
    ChaosGenSettings,
    ConnectSettings,
    DockerConnectSettings,
    UserHints,
)
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType


def _settings(**kwargs) -> ChaosGenSettings:
    return ChaosGenSettings(**kwargs)


class TestProfileValidation:
    def test_modular_monolith_requires_docker(self):
        s = _settings(
            hints=UserHints(
                architecture=ArchitectureType.MODULAR_MONOLITH,
                skip_auto_detect=True,
            ),
        )
        result = validate_profile_connect(s)
        assert not result.ok
        assert any("connect.docker" in e for e in result.errors)

    def test_modular_monolith_ok_with_compose_file(self):
        s = _settings(
            hints=UserHints(
                architecture=ArchitectureType.MODULAR_MONOLITH,
                skip_auto_detect=True,
            ),
            connect=ConnectSettings(
                docker=DockerConnectSettings(compose_file="labs/modular-monolith/docker-compose.yml"),
            ),
        )
        assert validate_profile_connect(s).ok

    def test_event_driven_requires_broker_bootstrap(self):
        s = _settings(
            hints=UserHints(
                architecture=ArchitectureType.EVENT_DRIVEN,
                skip_auto_detect=True,
            ),
        )
        result = validate_profile_connect(s)
        assert not result.ok
        assert any("connect.broker.bootstrap" in e for e in result.errors)

    def test_event_driven_ok_with_bootstrap(self):
        s = _settings(
            hints=UserHints(
                architecture=ArchitectureType.EVENT_DRIVEN,
                skip_auto_detect=True,
            ),
            connect=ConnectSettings(
                broker=BrokerConnectSettings(bootstrap="localhost:9092", type="redpanda"),
            ),
        )
        result = validate_profile_connect(s)
        assert result.ok
        assert result.tier == "P1"

    def test_client_server_requires_toxiproxy(self):
        s = _settings(
            hints=UserHints(
                architecture=ArchitectureType.CLIENT_SERVER,
                skip_auto_detect=True,
            ),
        )
        assert not validate_profile_connect(s).ok

    def test_require_valid_raises(self):
        s = _settings(
            hints=UserHints(
                architecture=ArchitectureType.EVENT_DRIVEN,
                skip_auto_detect=True,
            ),
        )
        try:
            require_valid_profile_connect(s)
            assert False, "expected ProfileValidationError"
        except ProfileValidationError as exc:
            assert exc.errors

    def test_modular_monolith_tier_p0(self):
        s = _settings(
            hints=UserHints(
                architecture=ArchitectureType.MODULAR_MONOLITH,
                environment=EnvironmentType.DOCKER_COMPOSE,
                skip_auto_detect=True,
            ),
            connect=ConnectSettings(docker=DockerConnectSettings(host="unix:///var/run/docker.sock")),
        )
        assert validate_profile_connect(s).tier == "P0"
