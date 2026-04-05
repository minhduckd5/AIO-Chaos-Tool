from enum import Enum
from typing import Dict, Any, List, Optional, Tuple
import platform
import subprocess
import os

from chaosgen.schemas.faults import ChaosExperiment, FaultType, TargetType, FaultSpec

class ExecutionEnvironment(str, Enum):
    KUBERNETES = "kubernetes"
    DOCKER = "docker"
    SYSTEMD = "systemd"  # Monolith/Linux Process
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
    Translates abstract fault specifications into tool-specific commands
    based on the execution environment.
    """

    def __init__(self, forced_env: Optional[ExecutionEnvironment] = None):
        self.env = forced_env or self._detect_environment()

    def _detect_environment(self) -> ExecutionEnvironment:
        """Detects the current execution environment."""
        # Check for Kubernetes
        if os.path.exists("/var/run/secrets/kubernetes.io"):
            return ExecutionEnvironment.KUBERNETES
        
        # Check for kubectl availability (if external controller)
        try:
            subprocess.run(["kubectl", "version", "--client"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Just having kubectl doesn't mean we are targeting k8s, but it's a hint. 
            # For now, let's rely on config or context. 
            # This is a simple heuristic.
        except FileNotFoundError:
            pass

        # Check for Docker
        try:
            subprocess.run(["docker", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return ExecutionEnvironment.DOCKER
        except FileNotFoundError:
            pass

        # Default to Systemd/Local for Linux
        if platform.system() == "Linux":
            return ExecutionEnvironment.SYSTEMD
        
        return ExecutionEnvironment.UNKNOWN

    def translate(self, experiment: ChaosExperiment) -> List[ActionPlan]:
        """
        Translate an abstract experiment into a list of executable actions.
        """
        plans = []
        for fault in experiment.faults:
            plan = self._map_fault_to_tool(experiment.target.type, fault, experiment.target)
            if plan:
                plans.append(plan)
        return plans

    def _map_fault_to_tool(self, target_type: TargetType, fault: FaultSpec, target_spec: Any) -> Optional[ActionPlan]:
        """Maps a single fault to a specific tool and action."""
        
        if self.env == ExecutionEnvironment.KUBERNETES:
            return self._map_k8s_fault(target_type, fault, target_spec)
        elif self.env == ExecutionEnvironment.DOCKER:
            return self._map_docker_fault(target_type, fault, target_spec)
        elif self.env == ExecutionEnvironment.SYSTEMD:
            return self._map_systemd_fault(target_type, fault, target_spec)
        
        return None

    def _map_k8s_fault(self, target_type: TargetType, fault: FaultSpec, target_spec: Any) -> ActionPlan:
        # Prefer Pumba or Chaos Mesh for K8s if available, or native kubectl wrapper
        if fault.fault_type == FaultType.PROCESS_KILL or fault.fault_type == FaultType.SERVICE_FAILURE or fault.fault_type == FaultType.NODE_FAILURE:
             # Use KubeMonkey or direct Pod deletion
             return ActionPlan(
                 tool_name="kube-monkey",
                 action="kill_pod",
                 params={
                     "label_selector": target_spec.selector,
                     "namespace": target_spec.namespace
                 }
             )
        # TODO: Add network fault mapping (e.g. to Pumba or TrafficControl)
        return ActionPlan("unknown", "unknown", {})

    def _map_docker_fault(self, target_type: TargetType, fault: FaultSpec, target_spec: Any) -> ActionPlan:
        if fault.fault_type == FaultType.PROCESS_KILL or fault.fault_type == FaultType.SERVICE_FAILURE:
            # Use Pumba for Docker Kill
            return ActionPlan(
                tool_name="pumba",
                action="kill",
                params={
                    "containers": [target_spec.name],
                    "signal": getattr(fault, "signal", "SIGKILL")
                }
            )
        elif fault.fault_type == FaultType.NETWORK_LATENCY:
            return ActionPlan(
                tool_name="pumba",
                action="netem_delay",
                params={
                    "containers": [target_spec.name],
                    "time": getattr(fault, "latency", "100ms"),
                    "jitter": getattr(fault, "jitter", "10ms"),
                    "duration": fault.duration
                }
            )
        return ActionPlan("unknown", "unknown", {})

    def _map_systemd_fault(self, target_type: TargetType, fault: FaultSpec, target_spec: Any) -> ActionPlan:
        if fault.fault_type == FaultType.PROCESS_KILL:
            return ActionPlan(
                tool_name="chaos-toolkit", # or a custom systemd module
                action="systemctl_stop",
                params={
                    "service_name": target_spec.name
                }
            )
        return ActionPlan("unknown", "unknown", {})

