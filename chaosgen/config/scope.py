"""
Project scope guards.

MODIFIED: Discovery is temporarily disabled. Pipeline focuses on MICROSERVICES
first; other architecture types (serverless, monolith, client-server, etc.)
will be re-enabled incrementally once the core Unknown→Known loop is stable.
"""

from __future__ import annotations

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

# Toggle full hybrid discovery (environment/architecture auto-probe).
DISCOVERY_ENABLED: bool = False

# Current research & implementation focus.
FOCUSED_ARCHITECTURE: ArchitectureType = ArchitectureType.MICROSERVICES
FOCUSED_ENVIRONMENT: EnvironmentType = EnvironmentType.KUBERNETES

_SCOPE_NOTICE = (
    "Discovery disabled — using focused microservices profile. "
    "Re-enable via chaosgen.config.scope.DISCOVERY_ENABLED when multi-arch "
    "support is ready."
)

# Representative microservices topology aligned with ScenarioCatalog target names.
_DEFAULT_MICROSERVICE_NODES = [
    ServiceNode(id="api-gateway", name="api-gateway", node_type="Deployment", namespace="default"),
    ServiceNode(id="downstream-service", name="downstream-service", node_type="Deployment", namespace="default"),
    ServiceNode(id="internal-service", name="internal-service", node_type="Deployment", namespace="default"),
    ServiceNode(id="app-pod", name="app-pod", node_type="Pod", namespace="default"),
]

_DEFAULT_MICROSERVICE_EDGES = [
    ServiceEdge(source="api-gateway", target="downstream-service", protocol="http"),
    ServiceEdge(source="downstream-service", target="internal-service", protocol="http"),
]


def build_focused_discovery_report(
    metrics_endpoint: str | None = None,
    logs_endpoint: str | None = None,
    traces_endpoint: str | None = None,
) -> DiscoveryReport:
    """
    Return a static DiscoveryReport for the microservices-focused pipeline.

    Avoids environment/architecture probing while keeping ContextBuilder and
    telemetry ingestion contracts unchanged.
    """
    from chaosgen.config.telemetry_endpoints import DEFAULT_LOKI_URL, DEFAULT_PROMETHEUS_URL

    metrics_endpoint = metrics_endpoint or DEFAULT_PROMETHEUS_URL
    logs_endpoint = logs_endpoint or DEFAULT_LOKI_URL

    service_map = ServiceMap(
        nodes=list(_DEFAULT_MICROSERVICE_NODES),
        edges=list(_DEFAULT_MICROSERVICE_EDGES),
        critical_paths=[
            ["api-gateway", "downstream-service", "internal-service"],
        ],
    )

    return DiscoveryReport(
        environment=EnvironmentProfile(
            type=FOCUSED_ENVIRONMENT,
            runtime_version="1.29.0",
            node_count=3,
            has_service_mesh=False,
        ),
        architecture=ArchitectureProfile(
            type=FOCUSED_ARCHITECTURE,
            service_count=len(_DEFAULT_MICROSERVICE_NODES),
            has_message_broker=False,
            has_api_gateway=True,
            confidence=1.0,
            signals=["Focused scope: microservices (discovery disabled)"],
        ),
        service_map=service_map,
        observability=ObservabilityProfile(
            has_metrics=True,
            metrics_endpoint=metrics_endpoint,
            has_logs=True,
            logs_endpoint=logs_endpoint,
            has_traces=traces_endpoint is not None,
            traces_endpoint=traces_endpoint,
            detected=[
                ObservabilityTool.PROMETHEUS,
                ObservabilityTool.LOKI,
            ],
            missing=[ObservabilityTool.GRAFANA, ObservabilityTool.JAEGER],
        ),
        signals=[
            DiscoverySignal(
                source=HintSource.HEURISTIC_FALLBACK,
                key="architecture_type",
                value=FOCUSED_ARCHITECTURE.value,
                confidence=1.0,
                message=_SCOPE_NOTICE,
            ),
        ],
    )


def scope_notice() -> str:
    return _SCOPE_NOTICE
