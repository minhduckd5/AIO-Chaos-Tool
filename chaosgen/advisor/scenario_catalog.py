"""
Scenario Catalog — pre-built, curated chaos scenarios indexed by ArchitectureType × FaultType.

Each entry is a fully-formed ChaosExperiment ready to pass through the HITL gate.
The catalog serves two purposes:
  1. Immediate value for users without telemetry (no LLM or anomaly data required)
  2. Seeds LLM generation with proven patterns to reduce hallucination risk

Usage:
    catalog = ScenarioCatalog()
    entries = catalog.get(ArchitectureType.MICROSERVICES, FaultType.NETWORK_LATENCY)
    all_ms = catalog.get_all(ArchitectureType.MICROSERVICES)
    results = catalog.search("kafka")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from chaosgen.schemas.discovery import ArchitectureType
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    NetworkFaultSpec,
    ProcessFaultSpec,
    ResourceFaultSpec,
    TargetSpec,
    TargetType,
)

logger = logging.getLogger(__name__)


@dataclass
class CatalogEntry:
    name: str
    description: str
    architecture: ArchitectureType
    fault_type: FaultType
    experiment_factory: Callable[[], ChaosExperiment]
    tags: list[str] = field(default_factory=list)

    def build(self) -> ChaosExperiment:
        return self.experiment_factory()


# ---------------------------------------------------------------------------
# Catalog definition
# ---------------------------------------------------------------------------

def _make_target(name: str, ttype: TargetType, namespace: str = "default") -> TargetSpec:
    return TargetSpec(type=ttype, name=name, namespace=namespace)


_CATALOG_ENTRIES: list[CatalogEntry] = [

    # -----------------------------------------------------------------------
    # MICROSERVICES × NETWORK
    # -----------------------------------------------------------------------

    CatalogEntry(
        name="Upstream dependency timeout cascade",
        description=(
            "Inject 2000ms latency on a downstream service to validate "
            "circuit breaker and timeout propagation behaviour."
        ),
        architecture=ArchitectureType.MICROSERVICES,
        fault_type=FaultType.NETWORK_LATENCY,
        tags=["circuit-breaker", "timeout", "cascading"],
        experiment_factory=lambda: ChaosExperiment(
            name="upstream-timeout-cascade",
            description="2s latency injection on downstream service",
            target=_make_target("downstream-service", TargetType.SERVICE),
            faults=[NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY,
                duration="60s",
                latency="2000ms",
                jitter="200ms",
            )],
            rollback=True,
        ),
    ),

    CatalogEntry(
        name="DNS resolution failure for internal service",
        description="Simulate DNS failure to test service discovery resilience.",
        architecture=ArchitectureType.MICROSERVICES,
        fault_type=FaultType.NETWORK_LATENCY,
        tags=["dns", "service-discovery"],
        experiment_factory=lambda: ChaosExperiment(
            name="dns-resolution-failure",
            description="DNS outage for internal service endpoint",
            target=_make_target("internal-service", TargetType.SERVICE),
            faults=[NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY,
                duration="30s",
                loss_percentage=100.0,
            )],
            rollback=True,
        ),
    ),

    CatalogEntry(
        name="Partial network partition between pods",
        description="50% packet loss between service pods — validates retry and backoff logic.",
        architecture=ArchitectureType.MICROSERVICES,
        fault_type=FaultType.PACKET_LOSS,
        tags=["partition", "retry", "backoff"],
        experiment_factory=lambda: ChaosExperiment(
            name="partial-partition",
            description="50% packet loss between service pods",
            target=_make_target("app-pod", TargetType.POD),
            faults=[NetworkFaultSpec(
                fault_type=FaultType.PACKET_LOSS,
                duration="90s",
                loss_percentage=50.0,
            )],
            rollback=True,
        ),
    ),

    # -----------------------------------------------------------------------
    # MICROSERVICES × PROCESS
    # -----------------------------------------------------------------------

    CatalogEntry(
        name="Replica reduction below minimum threshold",
        description=(
            "Kill pods to reduce replica count below the minimum — validates "
            "HPA and readiness probe failover behaviour."
        ),
        architecture=ArchitectureType.MICROSERVICES,
        fault_type=FaultType.PROCESS_KILL,
        tags=["hpa", "replica", "availability"],
        experiment_factory=lambda: ChaosExperiment(
            name="replica-reduction",
            description="Pod kill to test HPA and readiness failover",
            target=_make_target("app-pod", TargetType.POD),
            faults=[ProcessFaultSpec(
                fault_type=FaultType.PROCESS_KILL,
                duration="30s",
                signal="SIGKILL",
            )],
            rollback=True,
        ),
    ),

    CatalogEntry(
        name="OOM kill of memory-intensive service",
        description="Exhaust container memory to trigger OOM kill — validates restart policy.",
        architecture=ArchitectureType.MICROSERVICES,
        fault_type=FaultType.RESOURCE_EXHAUSTION,
        tags=["oom", "memory", "restart-policy"],
        experiment_factory=lambda: ChaosExperiment(
            name="oom-kill",
            description="Memory exhaustion to trigger OOM kill",
            target=_make_target("memory-heavy-service", TargetType.CONTAINER),
            faults=[ResourceFaultSpec(
                fault_type=FaultType.RESOURCE_EXHAUSTION,
                duration="45s",
                memory_percent=95,
            )],
            rollback=True,
        ),
    ),

    # -----------------------------------------------------------------------
    # MONOLITH × RESOURCE
    # -----------------------------------------------------------------------

    CatalogEntry(
        name="Disk fill to 95% under write-heavy load",
        description=(
            "Consume disk space to 95% capacity — validates graceful degradation "
            "and alerting when storage is near full."
        ),
        architecture=ArchitectureType.MONOLITH,
        fault_type=FaultType.RESOURCE_EXHAUSTION,
        tags=["disk", "storage", "alerting"],
        experiment_factory=lambda: ChaosExperiment(
            name="disk-fill-95",
            description="Disk fill to 95% on monolith host",
            target=_make_target("monolith-app", TargetType.PROCESS),
            faults=[ResourceFaultSpec(
                fault_type=FaultType.RESOURCE_EXHAUSTION,
                duration="60s",
                memory_percent=95,
            )],
            rollback=True,
        ),
    ),

    CatalogEntry(
        name="CPU starvation under concurrent requests",
        description=(
            "Pin CPU to 90% while generating concurrent load — validates "
            "request queuing, timeout behaviour, and autoscaling triggers."
        ),
        architecture=ArchitectureType.MONOLITH,
        fault_type=FaultType.RESOURCE_EXHAUSTION,
        tags=["cpu", "load", "autoscaling"],
        experiment_factory=lambda: ChaosExperiment(
            name="cpu-starvation",
            description="CPU starvation at 90% under concurrent requests",
            target=_make_target("monolith-app", TargetType.PROCESS),
            faults=[ResourceFaultSpec(
                fault_type=FaultType.RESOURCE_EXHAUSTION,
                duration="120s",
                cpu_percent=90,
            )],
            rollback=True,
        ),
    ),

    # -----------------------------------------------------------------------
    # EVENT-DRIVEN × NETWORK / PROCESS
    # -----------------------------------------------------------------------

    CatalogEntry(
        name="Kafka broker unavailability with consumer group",
        description=(
            "Kill Kafka broker process — validates consumer group rebalancing, "
            "offset commit behaviour, and dead-letter queue activation."
        ),
        architecture=ArchitectureType.EVENT_DRIVEN,
        fault_type=FaultType.PROCESS_KILL,
        tags=["kafka", "broker", "consumer-group", "dlq"],
        experiment_factory=lambda: ChaosExperiment(
            name="kafka-broker-kill",
            description="Kafka broker SIGKILL to test consumer failover",
            target=_make_target("kafka", TargetType.SERVICE),
            faults=[ProcessFaultSpec(
                fault_type=FaultType.PROCESS_KILL,
                duration="30s",
                signal="SIGKILL",
            )],
            rollback=True,
        ),
    ),

    CatalogEntry(
        name="Message flood beyond consumer processing capacity",
        description=(
            "Flood the message queue beyond consumer processing rate — "
            "validates backpressure, lag alerting, and DLQ saturation handling."
        ),
        architecture=ArchitectureType.EVENT_DRIVEN,
        fault_type=FaultType.NETWORK_LATENCY,
        tags=["flood", "backpressure", "lag", "dlq"],
        experiment_factory=lambda: ChaosExperiment(
            name="message-flood",
            description="Network throttle on consumer to simulate message flood",
            target=_make_target("message-consumer", TargetType.SERVICE),
            faults=[NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY,
                duration="90s",
                latency="500ms",
            )],
            rollback=True,
        ),
    ),

    # -----------------------------------------------------------------------
    # CLIENT-SERVER × NETWORK
    # -----------------------------------------------------------------------

    CatalogEntry(
        name="Server overload under concurrent client connections",
        description=(
            "Exhaust server CPU while multiple clients connect — validates "
            "connection queuing, rejection policy, and client timeout handling."
        ),
        architecture=ArchitectureType.CLIENT_SERVER,
        fault_type=FaultType.RESOURCE_EXHAUSTION,
        tags=["overload", "connections", "timeout"],
        experiment_factory=lambda: ChaosExperiment(
            name="server-cpu-overload",
            description="Server CPU starvation under concurrent connections",
            target=_make_target("server", TargetType.SERVICE),
            faults=[ResourceFaultSpec(
                fault_type=FaultType.RESOURCE_EXHAUSTION,
                duration="60s",
                cpu_percent=95,
            )],
            rollback=True,
        ),
    ),

    CatalogEntry(
        name="Network latency between client and server tiers",
        description="Add 500ms latency to client→server path — validates client timeout config.",
        architecture=ArchitectureType.CLIENT_SERVER,
        fault_type=FaultType.NETWORK_LATENCY,
        tags=["latency", "client-timeout"],
        experiment_factory=lambda: ChaosExperiment(
            name="client-server-latency",
            description="500ms latency on client-to-server network path",
            target=_make_target("server", TargetType.SERVICE),
            faults=[NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY,
                duration="60s",
                latency="500ms",
                jitter="50ms",
            )],
            rollback=True,
        ),
    ),

    # -----------------------------------------------------------------------
    # SERVERLESS × PROCESS / RESOURCE
    # -----------------------------------------------------------------------

    CatalogEntry(
        name="Function cold start timeout under concurrent invocations",
        description=(
            "Simulate cold start delay by injecting latency on downstream "
            "initialisation dependencies — validates timeout and retry config."
        ),
        architecture=ArchitectureType.SERVERLESS,
        fault_type=FaultType.NETWORK_LATENCY,
        tags=["cold-start", "concurrency", "timeout"],
        experiment_factory=lambda: ChaosExperiment(
            name="cold-start-timeout",
            description="Cold start simulation via downstream latency injection",
            target=_make_target("init-dependency", TargetType.SERVICE),
            faults=[NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY,
                duration="30s",
                latency="3000ms",
            )],
            rollback=True,
        ),
    ),
]


# ---------------------------------------------------------------------------
# ScenarioCatalog class
# ---------------------------------------------------------------------------


class ScenarioCatalog:
    def __init__(self) -> None:
        self._entries = _CATALOG_ENTRIES

    def get(
        self,
        architecture: ArchitectureType,
        fault_type: FaultType | None = None,
    ) -> list[CatalogEntry]:
        """Return entries matching the given architecture and optional fault type."""
        results = [e for e in self._entries if e.architecture == architecture]
        if fault_type is not None:
            results = [e for e in results if e.fault_type == fault_type]
        return results

    def get_all(self, architecture: ArchitectureType) -> list[CatalogEntry]:
        return self.get(architecture)

    def get_by_tags(self, *tags: str) -> list[CatalogEntry]:
        """Return entries that have ALL of the specified tags."""
        tag_set = set(tags)
        return [e for e in self._entries if tag_set.issubset(set(e.tags))]

    def search(self, query: str) -> list[CatalogEntry]:
        """Case-insensitive substring search across name, description, and tags."""
        q = query.lower()
        return [
            e for e in self._entries
            if q in e.name.lower()
            or q in e.description.lower()
            or any(q in tag for tag in e.tags)
        ]

    def all_architectures(self) -> list[ArchitectureType]:
        return sorted({e.architecture for e in self._entries}, key=lambda x: x.value)

    def __len__(self) -> int:
        return len(self._entries)
