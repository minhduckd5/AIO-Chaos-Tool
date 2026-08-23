from typing import Dict, Any, List, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field

from chaosgen.schemas.faults import ChaosExperiment, TargetType

if TYPE_CHECKING:
    from chaosgen.config.settings import SafetySettings


class SafetyPolicy(BaseModel):
    """Configuration for blast radius limits."""
    max_affected_nodes: int = Field(1, description="Maximum number of nodes that can be affected")
    max_affected_pods_percent: int = Field(20, description="Max percentage of pods affected")
    blocked_namespaces: List[str] = Field(default_factory=lambda: ["kube-system", "monitoring"], description="Namespaces where chaos is forbidden")
    blocked_services: List[str] = Field(default_factory=lambda: ["database-master"], description="Critical services to protect")

    @classmethod
    def from_settings(cls, settings: "SafetySettings | None") -> "SafetyPolicy":
        """MODIFIED: P8 — build policy from ChaosGenSettings.safety."""
        if settings is None:
            return cls()
        return cls(
            max_affected_nodes=settings.max_affected_nodes,
            max_affected_pods_percent=settings.max_affected_pods_percent,
            blocked_namespaces=list(settings.blocked_namespaces),
            blocked_services=list(settings.blocked_services),
        )

class BlastRadiusController:
    """
    Enforces safety policies to limit the scope of chaos experiments.
    """

    def __init__(self, policy: SafetyPolicy = None):
        self.policy = policy or SafetyPolicy()

    def validate_experiment(self, experiment: ChaosExperiment) -> bool:
        """
        Check if an experiment violates safety policies.
        
        Args:
            experiment: The proposed chaos experiment.
            
        Returns:
            bool: True if safe, False if policy violation.
        """
        target = experiment.target
        
        # Check blocked namespaces
        if target.namespace and target.namespace in self.policy.blocked_namespaces:
            raise ValueError(f"Safety Violation: Target namespace '{target.namespace}' is blocked.")

        # Check blocked services
        if target.name in self.policy.blocked_services:
             raise ValueError(f"Safety Violation: Target service '{target.name}' is protected.")

        # Heuristic checks for specific target types
        if target.type == TargetType.NODE:
             # If we had a count of total nodes, we could enforce percent.
             # For now, strict count check if multiple targets were supported (TargetSpec currently single named)
             # Future: Resolve selector to count targets.
             pass

        return True

    def validate_targets(self, targets: List[str]) -> bool:
        """
        Validate a resolved list of concrete targets (e.g. list of pod names).
        """
        if len(targets) > self.policy.max_affected_nodes: # interpreting broadly as "units"
             raise ValueError(f"Safety Violation: {len(targets)} targets exceeds limit of {self.policy.max_affected_nodes}")
        return True




