"""WS-5 — modular monolith lab artifacts smoke tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from chaosgen.config.profile_validation import require_valid_profile_connect
from chaosgen.config.settings import ChaosGenSettings, load_settings
from chaosgen.discovery import resolve_discovery_report
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType

_REPO = Path(__file__).resolve().parents[1]
_COMPOSE = _REPO / "labs" / "modular-monolith" / "docker-compose.yml"
_SETTINGS = _REPO / "examples" / "modular-monolith-settings.yaml"
_EXPECTATIONS = _REPO / "examples" / "modular-monolith-expectations.yaml"


def test_compose_stack_defines_profile_services():
    assert _COMPOSE.is_file()
    text = _COMPOSE.read_text(encoding="utf-8")
    for service in ("monolith-app", "module-api", "module-db", "prometheus"):
        assert f"{service}:" in text
    assert "name: chaosgen-monolith" in text


def test_modular_monolith_settings_load_and_validate():
    settings = load_settings(str(_SETTINGS))
    assert settings.hints.architecture == ArchitectureType.MODULAR_MONOLITH
    assert settings.hints.environment == EnvironmentType.DOCKER_COMPOSE
    assert settings.hints.skip_auto_detect is True
    assert settings.connect.docker.compose_file == "labs/modular-monolith/docker-compose.yml"
    assert settings.connect.docker.project_name == "chaosgen-monolith"
    assert settings.inject.dry_run is False
    require_valid_profile_connect(settings)


def test_resolve_discovery_report_not_microservices():
    settings = load_settings(str(_SETTINGS))
    report = resolve_discovery_report(settings)
    assert report.architecture.type == ArchitectureType.MODULAR_MONOLITH
    node_ids = {n.id for n in report.service_map.nodes}
    assert {"monolith-app", "module-api", "module-db"}.issubset(node_ids)


def test_expectations_yaml_has_required_checks():
    assert _EXPECTATIONS.is_file()
    data = yaml.safe_load(_EXPECTATIONS.read_text(encoding="utf-8"))
    assert data.get("claim")
    ids = {e["id"] for e in data.get("expectations", [])}
    assert "monolith_http_alive" in ids
    assert "monolith_probe_recovery" in ids
