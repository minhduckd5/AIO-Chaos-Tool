"""
Form-first profile connect validation (WS-2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from chaosgen.config.profile_presets import default_environment_for, profile_priority_tier
from chaosgen.config.settings import ChaosGenSettings
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType


class ProfileValidationError(ValueError):
    """Raised when hints + connect block fail profile requirements."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class ProfileValidationResult:
    architecture: ArchitectureType
    environment: EnvironmentType
    tier: str
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _non_empty(value: str | None) -> bool:
    return bool(value and str(value).strip())


def resolve_effective_environment(settings: ChaosGenSettings) -> EnvironmentType:
    hints = settings.hints
    if hints.environment is not None:
        return hints.environment
    if hints.architecture is not None:
        return default_environment_for(hints.architecture)
    return EnvironmentType.KUBERNETES


def _kubeconfig_path(settings: ChaosGenSettings) -> str | None:
    path = settings.connect.kubernetes.kubeconfig
    if _non_empty(path):
        return path.strip()
    legacy = settings.inject.kubeconfig
    if _non_empty(legacy):
        return legacy.strip()
    return None


def validate_profile_connect(settings: ChaosGenSettings) -> ProfileValidationResult:
    """
    Validate hints.architecture + connect block for form-first profile mode.

    Returns ProfileValidationResult with errors (empty tuple when valid).
    """
    hints = settings.hints
    arch = hints.architecture or ArchitectureType.MICROSERVICES
    env = resolve_effective_environment(settings)
    tier = profile_priority_tier(arch)
    errors: list[str] = []

    if hints.architecture is None:
        errors.append("Architecture profile is required (auto-detect disabled).")

    if arch == ArchitectureType.MICROSERVICES and env == EnvironmentType.KUBERNETES:
        if not _kubeconfig_path(settings):
            default_kube = Path.home() / ".kube" / "config"
            if not default_kube.is_file() and not os.environ.get("KUBECONFIG"):
                errors.append(
                    "Microservices on Kubernetes requires connect.kubernetes.kubeconfig "
                    "(or inject.kubeconfig / a default ~/.kube/config on this machine)."
                )

    if arch == ArchitectureType.MODULAR_MONOLITH:
        docker = settings.connect.docker
        if not (_non_empty(docker.host) or _non_empty(docker.compose_file)):
            errors.append(
                "Modular monolith (P0 live) requires connect.docker.host "
                "or connect.docker.compose_file."
            )

    if arch == ArchitectureType.MONOLITH and env == EnvironmentType.DOCKER_COMPOSE:
        docker = settings.connect.docker
        if not (_non_empty(docker.host) or _non_empty(docker.compose_file)):
            errors.append(
                "Monolith on Docker Compose requires connect.docker.host "
                "or connect.docker.compose_file."
            )

    if arch == ArchitectureType.EVENT_DRIVEN:
        if not _non_empty(settings.connect.broker.bootstrap):
            errors.append(
                "Event-driven profile requires connect.broker.bootstrap "
                "(e.g. localhost:9092)."
            )

    if arch == ArchitectureType.CLIENT_SERVER:
        if not _non_empty(settings.connect.toxiproxy.api_url):
            errors.append(
                "Client-server profile requires connect.toxiproxy.api_url "
                "(e.g. http://127.0.0.1:8474)."
            )

    return ProfileValidationResult(
        architecture=arch,
        environment=env,
        tier=tier,
        errors=tuple(errors),
    )


def require_valid_profile_connect(settings: ChaosGenSettings) -> ProfileValidationResult:
    result = validate_profile_connect(settings)
    if not result.ok:
        raise ProfileValidationError(list(result.errors))
    return result
