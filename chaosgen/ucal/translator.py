from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional
import platform
import subprocess
import os

from chaosgen.schemas.faults import ChaosExperiment, FaultType, TargetType, FaultSpec


class ExecutionEnvironment(str, Enum):
    KUBERNETES = "kubernetes"
    DOCKER = "docker"
    SYSTEMD = "systemd"
    UNKNOWN = "unknown"


class ActionPlan:
    """Represents a translated action ready for execution."""

    def __init__(self, tool_name: str, action: str, params: Dict[str, Any]):
        self.tool_name = tool_name
        self.action = action
        self.params = params


class ChaosTranslator:
    """
    Unified Chaos Abstraction Layer (UCAL) Translator.

    Form/CLI ``set_environment`` locks the tool path. Hint/auto-detect is only
    used when no operator environment has been set.
    """

    def __init__(
        self,
        forced_env: Optional[ExecutionEnvironment] = None,
        *,
        inject_settings: Any = None,
        hints_environment: Optional[str] = None,
    ):
        self.inject = inject_settings
        self.hints_environment = hints_environment
        # Form/CLI forced env wins. Auto-detect is legacy fallback only when unset.
        self._forced = forced_env is not None
        self.env = forced_env or self._detect_environment()

    def set_environment(self, env: ExecutionEnvironment) -> None:
        """Operator-selected environment (form-first). Disables further auto-detect."""
        self.env = env
        self._forced = True

    def _detect_environment(self) -> ExecutionEnvironment:
        """Legacy fallback when no form/CLI environment is set. Prefer set_environment()."""
        if self._forced:
            return self.env

        if self.hints_environment and str(self.hints_environment).lower() in (
            "kubernetes",
            "k8s",
            "k3s",
        ):
            return ExecutionEnvironment.KUBERNETES

        if self.hints_environment and str(self.hints_environment).lower() in (
            "docker_compose",
            "docker",
            "compose",
        ):
            return ExecutionEnvironment.DOCKER

        kubeconfig = None
        if self.inject is not None:
            kubeconfig = getattr(self.inject, "kubeconfig", None)
        if kubeconfig:
            expanded = Path(os.path.expanduser(str(kubeconfig)))
            if expanded.is_file() or getattr(self.inject, "enabled", False):
                return ExecutionEnvironment.KUBERNETES
        if os.environ.get("KUBECONFIG"):
            return ExecutionEnvironment.KUBERNETES

        if os.path.exists("/var/run/secrets/kubernetes.io"):
            return ExecutionEnvironment.KUBERNETES

        try:
            subprocess.run(
                ["kubectl", "version", "--client"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if self.hints_environment == "kubernetes" or (
                self.inject and getattr(self.inject, "enabled", False)
            ):
                return ExecutionEnvironment.KUBERNETES
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        try:
            subprocess.run(
                ["docker", "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            return ExecutionEnvironment.DOCKER
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        if platform.system() == "Linux":
            return ExecutionEnvironment.SYSTEMD

        return ExecutionEnvironment.UNKNOWN

    def translate(self, experiment: ChaosExperiment) -> List[ActionPlan]:
        plans: List[ActionPlan] = []
        for idx, fault in enumerate(experiment.faults):
            plan = self._map_fault_to_tool(
                experiment.target.type, fault, experiment.target, fault_index=idx
            )
            if plan is None:
                raise ValueError(
                    f"Unsupported fault {fault.fault_type.value} for env={self.env.value}"
                )
            if plan.tool_name == "unknown":
                raise ValueError(
                    f"No tool mapping for fault {fault.fault_type.value} "
                    f"on {self.env.value}"
                )
            plans.append(plan)
        return plans

    def _map_fault_to_tool(
        self,
        target_type: TargetType,
        fault: FaultSpec,
        target_spec: Any,
        *,
        fault_index: int = 0,
    ) -> Optional[ActionPlan]:
        if self.env == ExecutionEnvironment.KUBERNETES:
            return self._map_k8s_fault(target_type, fault, target_spec, fault_index)
        if self.env == ExecutionEnvironment.DOCKER:
            return self._map_docker_fault(target_type, fault, target_spec)
        if self.env == ExecutionEnvironment.SYSTEMD:
            return self._map_systemd_fault(target_type, fault, target_spec)
        return None

    def _selector(self, target_spec: Any) -> Dict[str, str]:
        if target_spec.selector:
            return dict(target_spec.selector)
        label_key = "app"
        if self.inject is not None:
            label_key = getattr(self.inject, "label_key", "app") or "app"
        return {label_key: target_spec.name}

    def _map_k8s_fault(
        self,
        target_type: TargetType,
        fault: FaultSpec,
        target_spec: Any,
        fault_index: int,
    ) -> ActionPlan:
        backend = "chaosmesh"
        if self.inject is not None:
            backend = getattr(self.inject, "chaos_backend", "chaosmesh") or "chaosmesh"

        selector = self._selector(target_spec)
        ns = target_spec.namespace or (
            getattr(self.inject, "default_namespace", "default") if self.inject else "default"
        )

        kill_types = {
            FaultType.PROCESS_KILL,
            FaultType.SERVICE_FAILURE,
            FaultType.NODE_FAILURE,
        }
        net_types = {FaultType.NETWORK_LATENCY, FaultType.PACKET_LOSS}

        if fault.fault_type in kill_types:
            if backend == "delete_pod":
                return ActionPlan(
                    tool_name="kubectl-chaos",
                    action="delete_pod",
                    params={
                        "namespace": ns,
                        "label_selector": selector,
                        "fault_index": fault_index,
                    },
                )
            return ActionPlan(
                tool_name="kubectl-chaos",
                action="apply_manifest",
                params={
                    "namespace": ns,
                    "label_selector": selector,
                    "fault_type": fault.fault_type.value,
                    "fault_index": fault_index,
                    "render": "chaosmesh",
                },
            )

        if fault.fault_type in net_types:
            return ActionPlan(
                tool_name="kubectl-chaos",
                action="apply_manifest",
                params={
                    "namespace": ns,
                    "label_selector": selector,
                    "fault_type": fault.fault_type.value,
                    "fault_index": fault_index,
                    "render": "chaosmesh",
                },
            )

        raise ValueError(f"Unsupported K8s fault type: {fault.fault_type}")

    def _map_docker_fault(
        self, target_type: TargetType, fault: FaultSpec, target_spec: Any
    ) -> ActionPlan:
        if fault.fault_type in (FaultType.PROCESS_KILL, FaultType.SERVICE_FAILURE):
            return ActionPlan(
                tool_name="pumba",
                action="kill_container",
                params={
                    "container": target_spec.name,
                    "signal": getattr(fault, "signal", "SIGKILL"),
                },
            )
        if fault.fault_type == FaultType.NETWORK_LATENCY:
            return ActionPlan(
                tool_name="pumba",
                action="delay_network",
                params={
                    "container": target_spec.name,
                    "delay": getattr(fault, "latency", "100ms"),
                    "duration": fault.duration,
                    "jitter": getattr(fault, "jitter", None),
                },
            )
        raise ValueError(f"Unsupported Docker fault: {fault.fault_type}")

    def _map_systemd_fault(
        self, target_type: TargetType, fault: FaultSpec, target_spec: Any
    ) -> ActionPlan:
        if fault.fault_type == FaultType.PROCESS_KILL:
            return ActionPlan(
                tool_name="chaos-toolkit",
                action="run_experiment",
                params={"service_name": target_spec.name},
            )
        raise ValueError(f"Unsupported systemd fault: {fault.fault_type}")
