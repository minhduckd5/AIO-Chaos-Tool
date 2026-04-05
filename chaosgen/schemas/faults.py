from enum import Enum
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, validator

class FaultType(str, Enum):
    NETWORK_LATENCY = "network_latency"
    PACKET_LOSS = "packet_loss"
    PROCESS_KILL = "process_kill"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    NODE_FAILURE = "node_failure"
    SERVICE_FAILURE = "service_failure"

class TargetType(str, Enum):
    CONTAINER = "container"
    POD = "pod"
    NODE = "node"
    PROCESS = "process"
    SERVICE = "service"

class TargetSpec(BaseModel):
    """Specification of the target for the fault injection."""
    type: TargetType
    name: str = Field(..., description="Name of the target (service name, pod label, etc.)")
    namespace: Optional[str] = Field(None, description="Namespace/Group for the target")
    selector: Optional[Dict[str, str]] = Field(None, description="Key-value pairs for selecting targets")

class FaultSpec(BaseModel):
    """Base specification for all faults."""
    fault_type: FaultType
    duration: str = Field("30s", description="Duration of the fault (e.g., 30s, 1m)")
    delay: Optional[str] = Field(None, description="Delay before injection")

class NetworkFaultSpec(FaultSpec):
    """Specification for network-related faults."""
    latency: Optional[str] = Field(None, description="Latency to inject (e.g., 100ms)")
    jitter: Optional[str] = Field(None, description="Jitter for latency")
    loss_percentage: Optional[float] = Field(None, description="Packet loss percentage")
    interface: str = Field("eth0", description="Network interface to target")

class ResourceFaultSpec(FaultSpec):
    """Specification for resource exhaustion faults."""
    cpu_percent: Optional[int] = Field(None, ge=0, le=100, description="CPU usage percentage")
    memory_percent: Optional[int] = Field(None, ge=0, le=100, description="Memory usage percentage")
    memory_bytes: Optional[str] = Field(None, description="Absolute memory to consume")

class ProcessFaultSpec(FaultSpec):
    """Specification for process-level faults."""
    signal: str = Field("SIGKILL", description="Signal to send to the process")
    grace_period: int = Field(0, description="Grace period before force kill")

class ChaosExperiment(BaseModel):
    """Complete definition of a chaos experiment."""
    name: str
    description: Optional[str] = None
    target: TargetSpec
    faults: List[FaultSpec]
    steady_state_check: Optional[Dict[str, Any]] = Field(None, description="Steady state hypothesis definition")
    rollback: bool = Field(True, description="Whether to rollback automatically")





