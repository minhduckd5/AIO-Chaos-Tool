"""
Discovery schemas for ChaosGen.

These Pydantic models represent the output of the discovery engine.
NetworkX DiGraph objects are always serialized via nx.node_link_data()
before being bound here — raw graph objects never appear in these models.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class EnvironmentType(str, Enum):
    KUBERNETES = "kubernetes"
    DOCKER_COMPOSE = "docker_compose"
    BARE_METAL = "bare_metal"
    CLOUD_VM = "cloud_vm"
    SERVERLESS = "serverless"
    UNKNOWN = "unknown"


class EnvironmentProfile(BaseModel):
    type: EnvironmentType
    runtime_version: str | None = None          # e.g. "1.29.0" for K8s, "24.0.5" for Docker
    namespace_count: int | None = None           # K8s only
    node_count: int | None = None                # K8s only
    cloud_provider: str | None = None            # "aws" | "gcp" | "azure" | None
    has_service_mesh: bool = False               # Istio / Linkerd detected
    constraints: dict[str, Any] = Field(default_factory=dict)  # raw extras


# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------


class ArchitectureType(str, Enum):
    MONOLITH = "monolith"
    MODULAR_MONOLITH = "modular_monolith"
    MICROSERVICES = "microservices"
    EVENT_DRIVEN = "event_driven"
    CLIENT_SERVER = "client_server"
    SERVERLESS = "serverless"
    UNKNOWN = "unknown"


class ArchitectureProfile(BaseModel):
    type: ArchitectureType
    service_count: int = 0
    has_message_broker: bool = False             # Kafka, RabbitMQ, NATS detected
    has_api_gateway: bool = False
    has_service_mesh: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    signals: list[str] = Field(default_factory=list)  # human-readable evidence


# ---------------------------------------------------------------------------
# Service Map  (Blind Spot 1: NetworkX serialization boundary)
# ---------------------------------------------------------------------------


class ServiceNode(BaseModel):
    """One vertex in the service dependency graph."""
    id: str
    name: str
    node_type: str = "service"           # "Deployment" | "StatefulSet" | "ExternalService" | "container"
    port: int | None = None
    namespace: str | None = None         # K8s only
    labels: dict[str, str] = Field(default_factory=dict)


class ServiceEdge(BaseModel):
    """One directed edge in the service dependency graph."""
    source: str                          # ServiceNode.id
    target: str
    protocol: str | None = None          # "http" | "grpc" | "tcp" | "amqp"
    weight: float = 1.0                  # call frequency weight if known


class ServiceMap(BaseModel):
    """
    JSON-serializable representation of the service dependency graph.

    The `node_link_data` field holds the output of nx.node_link_data(graph),
    which is a plain dict suitable for LLM context injection and REST payloads.
    The structured `nodes` / `edges` lists are used by the ranker and GUI.
    """
    nodes: list[ServiceNode] = Field(default_factory=list)
    edges: list[ServiceEdge] = Field(default_factory=list)
    node_link_data: dict[str, Any] = Field(
        default_factory=dict,
        description="nx.node_link_data(graph) output — JSON-safe, LLM-injectable",
    )
    critical_paths: list[list[str]] = Field(
        default_factory=list,
        description="Node ID sequences representing the most critical call paths",
    )


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class ObservabilityTool(str, Enum):
    PROMETHEUS = "prometheus"
    GRAFANA = "grafana"
    LOKI = "loki"
    JAEGER = "jaeger"
    OTEL_COLLECTOR = "otel_collector"


class ObservabilityProfile(BaseModel):
    has_metrics: bool = False
    metrics_endpoint: str | None = None         # e.g. "http://localhost:9090"
    has_logs: bool = False
    logs_endpoint: str | None = None
    has_traces: bool = False
    traces_endpoint: str | None = None
    detected: list[ObservabilityTool] = Field(default_factory=list)
    missing: list[ObservabilityTool] = Field(default_factory=list)

    @property
    def telemetry_ready(self) -> bool:
        """Minimum viable: metrics AND logs must both be present."""
        return self.has_metrics and self.has_logs


# ---------------------------------------------------------------------------
# Discovery signal types (Hybrid Discovery)
# ---------------------------------------------------------------------------


class HintSource(str, Enum):
    USER_OVERRIDE = "user_override"
    AUTO_DETECTED = "auto_detected"
    HEURISTIC_FALLBACK = "heuristic"


class ProbeOutcome(str, Enum):
    """Classifies *why* a probe succeeded or failed — critical for UX."""
    REACHABLE = "reachable"
    AUTH_REJECTED = "auth_rejected"
    CONNECTION_REFUSED = "connection_refused"
    TIMEOUT = "timeout"
    DNS_FAILURE = "dns_failure"
    UNKNOWN_ERROR = "unknown_error"


class DiscoverySignal(BaseModel):
    """
    One piece of evidence produced during discovery.
    Signals are consumed by the merge engine and surfaced in the GUI and LLM prompt.
    """
    source: HintSource
    key: str                                      # e.g. "architecture_type", "prometheus"
    value: str                                    # e.g. "microservices", "reachable"
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    flag: str | None = None                       # e.g. "USER_HEURISTIC_MISMATCH"
    probe_outcome: ProbeOutcome | None = None
    message: str = ""                             # human-readable detail


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------


class DiscoveryReport(BaseModel):
    """
    Full output of the discovery engine.
    Passed to context_builder.py to assemble ScenarioContext for LLM calls.
    """
    environment: EnvironmentProfile
    architecture: ArchitectureProfile
    service_map: ServiceMap
    observability: ObservabilityProfile
    signals: list[DiscoverySignal] = Field(
        default_factory=list,
        description="Structured evidence from all discovery phases",
    )
    discovery_errors: list[str] = Field(
        default_factory=list,
        description="Non-fatal probe errors encountered during discovery",
    )

    def summary_text(self) -> str:
        """Compact text representation for LLM prompt injection."""
        lines = [
            f"Environment: {self.environment.type.value} "
            f"(nodes={self.environment.node_count}, mesh={self.environment.has_service_mesh})",
            f"Architecture: {self.architecture.type.value} "
            f"(services={self.architecture.service_count}, "
            f"broker={self.architecture.has_message_broker})",
            f"Observability: metrics={self.observability.has_metrics}, "
            f"logs={self.observability.has_logs}, "
            f"traces={self.observability.has_traces}",
            f"Missing tools: {[t.value for t in self.observability.missing]}",
        ]
        mismatch = [s for s in self.signals if s.flag == "USER_HEURISTIC_MISMATCH"]
        if mismatch:
            lines.append("WARNINGS:")
            for s in mismatch:
                lines.append(f"  - {s.key}: {s.message}")
        auth_issues = [s for s in self.signals if s.probe_outcome == ProbeOutcome.AUTH_REJECTED]
        if auth_issues:
            lines.append("AUTH ISSUES:")
            for s in auth_issues:
                lines.append(f"  - {s.key}: tool reachable but auth rejected")
        return "\n".join(lines)
