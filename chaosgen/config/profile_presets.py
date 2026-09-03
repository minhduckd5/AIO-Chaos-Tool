"""
Form-first architecture profile presets (WS-1).

When DISCOVERY_ENABLED is False, pipeline consumers resolve DiscoveryReport from
operator-selected hints (settings.yaml / GUI) instead of heuristic classifiers.

Priority tiers (thesis defense):
  P0 live inject — microservices (K8s), modular_monolith (Docker)
  P1 catalog/dry-run — event_driven, monolith, client_server, serverless
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from chaosgen.schemas.discovery import (
    ArchitectureProfile,
    ArchitectureType,
    DiscoveryReport,
    DiscoverySignal,
    EnvironmentProfile,
    EnvironmentType,
    HintSource,
    ObservabilityProfile,
    ObservabilityTool,
    ServiceEdge,
    ServiceMap,
    ServiceNode,
)

if TYPE_CHECKING:
    from chaosgen.config.settings import ChaosGenSettings

# ---------------------------------------------------------------------------
# Priority matrix (documentation + downstream UX)
# ---------------------------------------------------------------------------

P0_LIVE_ARCHITECTURES = frozenset({
    ArchitectureType.MICROSERVICES,
    ArchitectureType.MODULAR_MONOLITH,
})

P1_DRY_RUN_ARCHITECTURES = frozenset({
    ArchitectureType.EVENT_DRIVEN,
    ArchitectureType.MONOLITH,
    ArchitectureType.CLIENT_SERVER,
    ArchitectureType.SERVERLESS,
})


def profile_priority_tier(architecture: ArchitectureType) -> str:
    """Return 'P0' (live inject) or 'P1' (catalog/dry-run)."""
    if architecture in P0_LIVE_ARCHITECTURES:
        return "P0"
    return "P1"


# ---------------------------------------------------------------------------
# Static service maps — names align with scenario_catalog.py targets
# ---------------------------------------------------------------------------

_MICROSERVICE_NODES = [
    ServiceNode(id="api-gateway", name="api-gateway", node_type="Deployment", namespace="default"),
    ServiceNode(id="downstream-service", name="downstream-service", node_type="Deployment", namespace="default"),
    ServiceNode(id="internal-service", name="internal-service", node_type="Deployment", namespace="default"),
    ServiceNode(id="app-pod", name="app-pod", node_type="Pod", namespace="default"),
]

_MICROSERVICE_EDGES = [
    ServiceEdge(source="api-gateway", target="downstream-service", protocol="http"),
    ServiceEdge(source="downstream-service", target="internal-service", protocol="http"),
]

_MODULAR_MONOLITH_NODES = [
    ServiceNode(id="monolith-app", name="monolith-app", node_type="container"),
    ServiceNode(id="module-api", name="module-api", node_type="container"),
    ServiceNode(id="module-db", name="module-db", node_type="container"),
]

_MODULAR_MONOLITH_EDGES = [
    ServiceEdge(source="module-api", target="module-db", protocol="tcp"),
]

_MONOLITH_NODES = [
    ServiceNode(id="monolith-app", name="monolith-app", node_type="process"),
]

_EVENT_DRIVEN_NODES = [
    ServiceNode(id="kafka", name="kafka", node_type="container"),
    ServiceNode(id="message-consumer", name="message-consumer", node_type="container"),
    ServiceNode(id="message-producer", name="message-producer", node_type="container"),
]

_EVENT_DRIVEN_EDGES = [
    ServiceEdge(source="message-producer", target="kafka", protocol="amqp"),
    ServiceEdge(source="kafka", target="message-consumer", protocol="amqp"),
]

_CLIENT_SERVER_NODES = [
    ServiceNode(id="server", name="server", node_type="service"),
    ServiceNode(id="client", name="client", node_type="service"),
]

_CLIENT_SERVER_EDGES = [
    ServiceEdge(source="client", target="server", protocol="http"),
]

_SERVERLESS_NODES = [
    ServiceNode(id="init-dependency", name="init-dependency", node_type="ExternalService"),
    ServiceNode(id="function-runtime", name="function-runtime", node_type="service"),
]

_SERVERLESS_EDGES = [
    ServiceEdge(source="function-runtime", target="init-dependency", protocol="http"),
]


@dataclass(frozen=True)
class _ProfilePreset:
    default_environment: EnvironmentType
    architecture: ArchitectureProfile
    service_map: ServiceMap


def _arch_profile(
    arch: ArchitectureType,
    *,
    service_count: int,
    has_message_broker: bool = False,
    has_api_gateway: bool = False,
    has_service_mesh: bool = False,
    extra_signals: list[str] | None = None,
) -> ArchitectureProfile:
    signals = [
        f"Form-first profile: {arch.value}",
        f"priority_tier={profile_priority_tier(arch)}",
    ]
    if extra_signals:
        signals.extend(extra_signals)
    return ArchitectureProfile(
        type=arch,
        service_count=service_count,
        has_message_broker=has_message_broker,
        has_api_gateway=has_api_gateway,
        has_service_mesh=has_service_mesh,
        confidence=1.0,
        signals=signals,
    )


def _env_profile(env: EnvironmentType) -> EnvironmentProfile:
    if env == EnvironmentType.KUBERNETES:
        return EnvironmentProfile(
            type=env,
            runtime_version="1.29.0",
            node_count=3,
            has_service_mesh=False,
        )
    if env == EnvironmentType.DOCKER_COMPOSE:
        return EnvironmentProfile(type=env, runtime_version="24.0")
    if env == EnvironmentType.SERVERLESS:
        return EnvironmentProfile(type=env, cloud_provider="local")
    return EnvironmentProfile(type=env)


def _service_map(
    nodes: list[ServiceNode],
    edges: list[ServiceEdge] | None = None,
    critical_paths: list[list[str]] | None = None,
) -> ServiceMap:
    return ServiceMap(
        nodes=list(nodes),
        edges=list(edges or []),
        critical_paths=list(critical_paths or []),
    )


# (architecture) -> preset; environment may be overridden by hints.environment
_PROFILE_BY_ARCHITECTURE: dict[ArchitectureType, _ProfilePreset] = {
    ArchitectureType.MICROSERVICES: _ProfilePreset(
        default_environment=EnvironmentType.KUBERNETES,
        architecture=_arch_profile(
            ArchitectureType.MICROSERVICES,
            service_count=len(_MICROSERVICE_NODES),
            has_api_gateway=True,
        ),
        service_map=_service_map(
            _MICROSERVICE_NODES,
            _MICROSERVICE_EDGES,
            [["api-gateway", "downstream-service", "internal-service"]],
        ),
    ),
    ArchitectureType.MODULAR_MONOLITH: _ProfilePreset(
        default_environment=EnvironmentType.DOCKER_COMPOSE,
        architecture=_arch_profile(
            ArchitectureType.MODULAR_MONOLITH,
            service_count=len(_MODULAR_MONOLITH_NODES),
        ),
        service_map=_service_map(
            _MODULAR_MONOLITH_NODES,
            _MODULAR_MONOLITH_EDGES,
            [["module-api", "module-db"]],
        ),
    ),
    ArchitectureType.MONOLITH: _ProfilePreset(
        default_environment=EnvironmentType.DOCKER_COMPOSE,
        architecture=_arch_profile(
            ArchitectureType.MONOLITH,
            service_count=len(_MONOLITH_NODES),
        ),
        service_map=_service_map(_MONOLITH_NODES),
    ),
    ArchitectureType.EVENT_DRIVEN: _ProfilePreset(
        default_environment=EnvironmentType.DOCKER_COMPOSE,
        architecture=_arch_profile(
            ArchitectureType.EVENT_DRIVEN,
            service_count=len(_EVENT_DRIVEN_NODES),
            has_message_broker=True,
            extra_signals=["connect.broker required for event-driven profile"],
        ),
        service_map=_service_map(
            _EVENT_DRIVEN_NODES,
            _EVENT_DRIVEN_EDGES,
            [["message-producer", "kafka", "message-consumer"]],
        ),
    ),
    ArchitectureType.CLIENT_SERVER: _ProfilePreset(
        default_environment=EnvironmentType.DOCKER_COMPOSE,
        architecture=_arch_profile(
            ArchitectureType.CLIENT_SERVER,
            service_count=len(_CLIENT_SERVER_NODES),
        ),
        service_map=_service_map(
            _CLIENT_SERVER_NODES,
            _CLIENT_SERVER_EDGES,
            [["client", "server"]],
        ),
    ),
    ArchitectureType.SERVERLESS: _ProfilePreset(
        default_environment=EnvironmentType.SERVERLESS,
        architecture=_arch_profile(
            ArchitectureType.SERVERLESS,
            service_count=len(_SERVERLESS_NODES),
        ),
        service_map=_service_map(
            _SERVERLESS_NODES,
            _SERVERLESS_EDGES,
            [["function-runtime", "init-dependency"]],
        ),
    ),
}


def default_environment_for(architecture: ArchitectureType) -> EnvironmentType:
    preset = _PROFILE_BY_ARCHITECTURE.get(architecture)
    if preset is None:
        return EnvironmentType.UNKNOWN
    return preset.default_environment


def build_preset_discovery_report(
    architecture: ArchitectureType,
    environment: EnvironmentType | None = None,
    *,
    metrics_endpoint: str | None = None,
    logs_endpoint: str | None = None,
    traces_endpoint: str | None = None,
    service_overrides: list[str] | None = None,
    source_message: str = "Profile preset (form-first)",
) -> DiscoveryReport:
    """
    Build a DiscoveryReport from a static preset.

    Used by build_focused_discovery_report (microservices default) and
    build_profile_from_hints (operator-selected profile).
    """
    from chaosgen.config.telemetry_endpoints import DEFAULT_LOKI_URL, DEFAULT_PROMETHEUS_URL

    preset = _PROFILE_BY_ARCHITECTURE.get(architecture)
    errors: list[str] = []
    if preset is None:
        errors.append(f"No preset for architecture {architecture.value}")
        return DiscoveryReport(
            environment=EnvironmentProfile(type=EnvironmentType.UNKNOWN),
            architecture=ArchitectureProfile(type=ArchitectureType.UNKNOWN, confidence=0.0),
            service_map=ServiceMap(),
            observability=ObservabilityProfile(),
            signals=[
                DiscoverySignal(
                    source=HintSource.HEURISTIC_FALLBACK,
                    key="architecture_type",
                    value=ArchitectureType.UNKNOWN.value,
                    message=source_message,
                ),
            ],
            discovery_errors=errors,
        )

    env_type = environment or preset.default_environment
    if environment is not None and environment != preset.default_environment:
        errors.append(
            f"Environment {environment.value} differs from typical default "
            f"{preset.default_environment.value} for {architecture.value}"
        )

    service_map = preset.service_map.model_copy(deep=True)
    if service_overrides:
        existing = {n.id for n in service_map.nodes}
        for name in service_overrides:
            if name not in existing:
                service_map.nodes.append(
                    ServiceNode(id=name, name=name, node_type="service"),
                )

    metrics_endpoint = metrics_endpoint or DEFAULT_PROMETHEUS_URL
    logs_endpoint = logs_endpoint or DEFAULT_LOKI_URL

    return DiscoveryReport(
        environment=_env_profile(env_type),
        architecture=preset.architecture.model_copy(deep=True),
        service_map=service_map,
        observability=ObservabilityProfile(
            has_metrics=True,
            metrics_endpoint=metrics_endpoint,
            has_logs=True,
            logs_endpoint=logs_endpoint,
            has_traces=traces_endpoint is not None,
            traces_endpoint=traces_endpoint,
            detected=[ObservabilityTool.PROMETHEUS, ObservabilityTool.LOKI],
            missing=[ObservabilityTool.GRAFANA, ObservabilityTool.JAEGER],
        ),
        signals=[
            DiscoverySignal(
                source=HintSource.USER_OVERRIDE,
                key="architecture_type",
                value=architecture.value,
                confidence=1.0,
                message=source_message,
            ),
            DiscoverySignal(
                source=HintSource.USER_OVERRIDE,
                key="environment_type",
                value=env_type.value,
                confidence=1.0,
                message=f"Profile environment: {env_type.value}",
            ),
            DiscoverySignal(
                source=HintSource.USER_OVERRIDE,
                key="profile_tier",
                value=profile_priority_tier(architecture),
                message=f"Priority tier: {profile_priority_tier(architecture)}",
            ),
        ],
        discovery_errors=errors,
    )


def build_profile_from_hints(settings: ChaosGenSettings) -> DiscoveryReport:
    """
    Resolve DiscoveryReport from operator form hints (no heuristic classifier).

    Requires hints.architecture. Environment falls back to preset default when unset.
    Merges observability probe results when hints.observability is configured.
    """
    from chaosgen.discovery.observability_probe import ObservabilityProbe

    hints = settings.hints
    if hints.architecture is None:
        raise ValueError("hints.architecture is required for form-first profile resolution")

    architecture = hints.architecture
    environment = hints.environment or default_environment_for(architecture)

    report = build_preset_discovery_report(
        architecture,
        environment,
        service_overrides=hints.services or None,
        source_message="Profile loaded from settings form (auto-detect disabled)",
    )

    if hints.observability:
        obs_probe = ObservabilityProbe(hints=hints.observability)
        obs_profile, obs_signals = obs_probe.probe()
        report.observability = obs_profile
        report.signals.extend(obs_signals)

    return report
