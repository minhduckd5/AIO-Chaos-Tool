import logging
from typing import Dict, List, Optional

from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultSpec,
    FaultType,
    NetworkFaultSpec,
    ProcessFaultSpec,
    ResourceFaultSpec,
    TargetSpec,
    TargetType,
)
from chaosgen.schemas.scenarios import (
    FaultHypothesis,
    ScenarioComplexityIndex,
)
from chaosgen.safety.governance import BlastRadiusController, SafetyPolicy

logger = logging.getLogger(__name__)


class ScenarioGenerator:
    """
    Maps LLM-generated FaultHypotheses into executable ChaosExperiment
    objects, enforcing confidence thresholds and blast radius safety.
    """

    def __init__(
        self,
        safety_policy: Optional[SafetyPolicy] = None,
        confidence_threshold: float = 0.6,
    ):
        self.confidence_threshold = confidence_threshold
        self.blast_radius = BlastRadiusController(safety_policy)

    def generate(
        self,
        hypotheses: List[FaultHypothesis],
        available_targets: Optional[Dict[str, TargetSpec]] = None,
    ) -> List[ChaosExperiment]:
        """
        Convert validated hypotheses into ChaosExperiment objects.
        Low-confidence hypotheses are dropped; unsafe experiments are rejected.
        """
        accepted = self._filter_by_confidence(hypotheses)
        experiments: List[ChaosExperiment] = []

        for hyp in accepted:
            try:
                target = self._resolve_target(hyp.target_hint, available_targets)
                fault = self._map_hypothesis_to_fault(hyp)
                experiment = ChaosExperiment(
                    name=f"ai-{hyp.fault_type.value}-{hyp.target_hint}",
                    description=hyp.rationale,
                    target=target,
                    faults=[fault],
                    steady_state_check={"http_health": f"http://{hyp.target_hint}:8080/health"},
                    rollback=True,
                )
                self.blast_radius.validate_experiment(experiment)
                experiments.append(experiment)
                logger.info(
                    "Generated experiment '%s' (SCI=%.3f)",
                    experiment.name,
                    self.compute_sci(experiment).compute(),
                )
            except ValueError as e:
                logger.warning("Safety rejected hypothesis for '%s': %s",
                               hyp.target_hint, e)
            except Exception as e:
                logger.error("Failed to generate experiment from hypothesis: %s", e)

        return experiments

    def _filter_by_confidence(
        self, hypotheses: List[FaultHypothesis]
    ) -> List[FaultHypothesis]:
        """Drop hypotheses below the confidence threshold."""
        accepted = []
        for hyp in hypotheses:
            if hyp.confidence >= self.confidence_threshold:
                accepted.append(hyp)
            else:
                logger.info(
                    "SKIPPED_LOW_CONFIDENCE: cluster=%d, fault=%s, confidence=%.2f < %.2f, rationale='%s'",
                    hyp.source_cluster_id,
                    hyp.fault_type.value,
                    hyp.confidence,
                    self.confidence_threshold,
                    hyp.rationale,
                )
        return accepted

    @staticmethod
    def _map_hypothesis_to_fault(hyp: FaultHypothesis) -> FaultSpec:
        """Convert a FaultHypothesis into the appropriate FaultSpec subclass."""
        params = hyp.suggested_parameters
        duration = hyp.suggested_duration

        if hyp.fault_type == FaultType.NETWORK_LATENCY:
            return NetworkFaultSpec(
                fault_type=hyp.fault_type,
                duration=duration,
                latency=params.get("latency", "100ms"),
                jitter=params.get("jitter", "10ms"),
                loss_percentage=float(params.get("loss_percentage", 0)),
            )
        elif hyp.fault_type == FaultType.PACKET_LOSS:
            return NetworkFaultSpec(
                fault_type=hyp.fault_type,
                duration=duration,
                loss_percentage=float(params.get("loss_percentage", 10)),
            )
        elif hyp.fault_type == FaultType.RESOURCE_EXHAUSTION:
            return ResourceFaultSpec(
                fault_type=hyp.fault_type,
                duration=duration,
                cpu_percent=int(params.get("cpu_percent", 80)),
                memory_percent=int(params.get("memory_percent", 0)) or None,
            )
        elif hyp.fault_type in (FaultType.PROCESS_KILL, FaultType.SERVICE_FAILURE):
            return ProcessFaultSpec(
                fault_type=hyp.fault_type,
                duration=duration,
                signal=params.get("signal", "SIGKILL"),
                grace_period=int(params.get("grace_period", 0)),
            )
        else:
            return FaultSpec(fault_type=hyp.fault_type, duration=duration)

    @staticmethod
    def _resolve_target(
        hint: str,
        available_targets: Optional[Dict[str, TargetSpec]],
    ) -> TargetSpec:
        """Resolve a service name hint into a concrete TargetSpec."""
        if available_targets and hint in available_targets:
            return available_targets[hint]

        return TargetSpec(
            type=TargetType.SERVICE,
            name=hint,
            namespace="default",
        )

    @staticmethod
    def compute_sci(experiment: ChaosExperiment) -> ScenarioComplexityIndex:
        """Compute the Scenario Complexity Index for an experiment."""
        fault_types = {f.fault_type for f in experiment.faults}
        return ScenarioComplexityIndex(
            fault_cardinality=len(fault_types),
            target_diversity=1,
            temporal_stages=len(experiment.faults),
            blast_radius_percent=0.0,
            causal_chain_depth=1,
        )
