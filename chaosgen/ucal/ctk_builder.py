"""
Build Chaos Toolkit experiments from ChaosGen GUI / advisor intents.

Fault-centric: each fault contains a subset of targets (``faultN → targets_n``).
Builder expands to sequential ``method[]`` actions (cascade default).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from chaosgen.schemas.chaos_intent import CtkExperimentIntent, CtkFaultIntent, CtkTargetRef
from chaosgen.schemas.ctk_experiment import CtkActivity, CtkExperiment, CtkSteadyStateHypothesis
from chaosgen.schemas.faults import FaultType


@dataclass
class CtkTargetIntent:
    service: str
    namespace: str = "default"
    label_key: str = "app"
    fault_type: str = FaultType.PROCESS_KILL.value
    duration: str = "30s"
    latency: str = "100ms"
    loss_percentage: float = 10.0
    signal: str = "SIGKILL"


@dataclass
class CtkBuildIntent:
    title: str
    description: str = ""
    targets: List[CtkTargetIntent] = field(default_factory=list)
    tags: List[str] = field(default_factory=lambda: ["chaosgen"])
    prom_url: Optional[str] = None
    prom_query: str = 'up{job=~".+"}'
    include_steady_state: bool = False
    max_actions: int = 3
    blocked_namespaces: Sequence[str] = field(
        default_factory=lambda: ("kube-system", "monitoring")
    )
    kube_context: Optional[str] = None
    action_pause_seconds: float = 0
    auto_rollback: bool = True


def _label_selector(t: CtkTargetIntent) -> str:
    return f"{t.label_key}={t.service}"


def _network_chaos_name(fault_type: str, service: str) -> str:
    ft = (fault_type or "").lower()
    if ft in (FaultType.PACKET_LOSS.value, "packet_loss"):
        return f"chaosgen-loss-{service}"
    return f"chaosgen-latency-{service}"


def _terminate_action(t: CtkTargetIntent) -> CtkActivity:
    return CtkActivity(
        type="action",
        name=f"terminate-{t.service}",
        provider={
            "type": "python",
            "module": "chaosk8s.pod.actions",
            "func": "terminate_pods",
            "arguments": {
                "label_selector": _label_selector(t),
                "ns": t.namespace,
                "rand": False,
                "qty": 1,
            },
        },
        secrets=["k8s"],
    )


def _network_latency_action(t: CtkTargetIntent) -> CtkActivity:
    return CtkActivity(
        type="action",
        name=f"latency-{t.service}",
        provider={
            "type": "python",
            "module": "chaosk8s.chaosmesh.network.actions",
            "func": "add_latency",
            "arguments": {
                "name": _network_chaos_name("network_latency", t.service),
                "ns": t.namespace,
                "label_selectors": {t.label_key: t.service},
                "latency": t.latency,
            },
        },
        secrets=["k8s"],
    )


def _packet_loss_action(t: CtkTargetIntent) -> CtkActivity:
    return CtkActivity(
        type="action",
        name=f"loss-{t.service}",
        provider={
            "type": "python",
            "module": "chaosk8s.chaosmesh.network.actions",
            "func": "set_loss",
            "arguments": {
                "name": _network_chaos_name("packet_loss", t.service),
                "ns": t.namespace,
                "label_selectors": {t.label_key: t.service},
                "loss": str(float(t.loss_percentage)),
            },
        },
        secrets=["k8s"],
    )


def _network_rollback_action(t: CtkTargetIntent) -> CtkActivity:
    name = _network_chaos_name(t.fault_type, t.service)
    return CtkActivity(
        type="action",
        name=f"rollback-{name}",
        provider={
            "type": "python",
            "module": "chaosk8s.chaosmesh.network.actions",
            "func": "delete_network_fault",
            "arguments": {"name": name, "ns": t.namespace},
        },
        secrets=["k8s"],
    )


def _action_for_target(t: CtkTargetIntent) -> CtkActivity:
    ft = (t.fault_type or "").lower()
    if ft in (
        FaultType.PROCESS_KILL.value,
        FaultType.SERVICE_FAILURE.value,
        FaultType.NODE_FAILURE.value,
        "process_kill",
        "pod_kill",
    ):
        return _terminate_action(t)
    if ft in (FaultType.NETWORK_LATENCY.value, "network_latency"):
        return _network_latency_action(t)
    if ft in (FaultType.PACKET_LOSS.value, "packet_loss"):
        return _packet_loss_action(t)
    raise ValueError(f"Unsupported fault_type for CTK builder: {t.fault_type}")


def _is_network_fault(fault_type: str) -> bool:
    ft = (fault_type or "").lower()
    return ft in (
        FaultType.NETWORK_LATENCY.value,
        FaultType.PACKET_LOSS.value,
        "network_latency",
        "packet_loss",
    )


def _expand_fault_targets(fault: CtkFaultIntent) -> List[CtkTargetIntent]:
    return [
        CtkTargetIntent(
            service=ref.service,
            namespace=ref.namespace,
            label_key=ref.label_key,
            fault_type=fault.fault_type,
            duration=fault.duration,
            latency=fault.latency,
            loss_percentage=fault.loss_percentage,
            signal=fault.signal,
        )
        for ref in fault.targets
    ]


def _intent_from_legacy(build: CtkBuildIntent) -> CtkExperimentIntent:
    if not build.targets:
        raise ValueError("CTK build requires at least one target")
    buckets: Dict[str, Dict[str, Any]] = {}
    for t in build.targets:
        key = (
            f"{t.fault_type}|{t.duration}|{t.latency}|{t.loss_percentage}|{t.signal}"
        )
        if key not in buckets:
            buckets[key] = {
                "fault_type": t.fault_type,
                "duration": t.duration,
                "latency": t.latency,
                "loss_percentage": t.loss_percentage,
                "signal": t.signal,
                "targets": [],
            }
        buckets[key]["targets"].append(
            CtkTargetRef(
                service=t.service,
                namespace=t.namespace,
                label_key=t.label_key,
            )
        )
    faults = [CtkFaultIntent(**bucket) for bucket in buckets.values()]
    return CtkExperimentIntent(
        title=build.title,
        description=build.description,
        faults=faults,
        action_pause_seconds=build.action_pause_seconds,
        include_steady_state=build.include_steady_state,
        prom_url=build.prom_url,
        tags=list(build.tags or []),
        max_actions=build.max_actions,
        blocked_namespaces=list(build.blocked_namespaces),
        kube_context=build.kube_context,
        auto_rollback=build.auto_rollback,
    )


def build_experiment_from_intent(intent: CtkExperimentIntent) -> CtkExperiment:
    """Build CTK experiment from fault-centric intent (``faults[]`` / ``targets[]``)."""
    if not intent.faults:
        raise ValueError("CTK build requires at least one fault")

    expanded: List[CtkTargetIntent] = []
    for fault in intent.faults:
        expanded.extend(_expand_fault_targets(fault))

    if not expanded:
        raise ValueError("CTK build requires at least one target")
    if len(expanded) > int(intent.max_actions):
        raise ValueError(
            f"total actions {len(expanded)} exceeds max_actions={intent.max_actions}"
        )

    method: List[CtkActivity] = []
    rollbacks: List[CtkActivity] = []
    pause_s = float(intent.action_pause_seconds or 0)

    for idx, t in enumerate(expanded):
        ns = (t.namespace or "default").strip()
        if ns in set(intent.blocked_namespaces):
            raise ValueError(f"namespace {ns!r} is blocked by safety policy")
        activity = _action_for_target(t)
        if idx > 0 and pause_s > 0:
            activity.pauses = {"after": int(pause_s)}
        method.append(activity)
        if intent.auto_rollback and _is_network_fault(t.fault_type):
            rollbacks.append(_network_rollback_action(t))

    tags = list(intent.tags or [])
    if "chaosgen" not in tags:
        tags.append("chaosgen")

    secrets: Optional[Dict[str, Any]] = None
    if intent.kube_context:
        secrets = {"k8s": {"KUBERNETES_CONTEXT": intent.kube_context}}

    hypothesis = None
    if intent.include_steady_state and intent.prom_url:
        hypothesis = CtkSteadyStateHypothesis(
            title="Prometheus reachable",
            probes=[
                CtkActivity(
                    type="probe",
                    name="prom-ready",
                    tolerance=200,
                    provider={
                        "type": "http",
                        "url": intent.prom_url.rstrip("/") + "/-/ready",
                        "timeout": 5,
                    },
                )
            ],
        )

    return CtkExperiment(
        title=intent.title,
        description=intent.description or intent.title,
        method=method,
        tags=tags,
        secrets=secrets,
        steady_state_hypothesis=hypothesis,
        rollbacks=rollbacks or None,
    )


def build_experiment(intent: CtkBuildIntent) -> CtkExperiment:
    """Translate legacy flat-target intent or build from ``CtkBuildIntent``."""
    return build_experiment_from_intent(_intent_from_legacy(intent))
