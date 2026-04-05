from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from chaosgen.schemas.faults import ChaosExperiment, FaultType


class AnomalySeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnomalyCluster(BaseModel):
    """Cluster of related anomalous behaviors detected by the ML pipeline."""
    cluster_id: int
    severity: AnomalySeverity
    affected_services: List[str]
    dominant_features: List[Tuple[str, float]] = Field(
        description="Feature name and its deviation z-score, sorted descending"
    )
    sample_count: int
    sample_timestamps: List[float] = Field(default_factory=list)
    centroid: Optional[List[float]] = None


class AnomalySummary(BaseModel):
    """
    Compact representation of an AnomalyCluster for LLM consumption.
    Designed to fit within ~200-300 tokens per cluster to respect
    context window limits on 8B-class local models.
    """
    service_name: str
    top_features: List[Tuple[str, float]] = Field(
        max_length=3,
        description="Top 3 deviating metrics: (metric_name, z_score)"
    )
    error_pattern: Optional[str] = Field(
        None,
        description="Dominant log error pattern (regex or keyword)"
    )
    severity: float = Field(ge=0.0, le=1.0, description="Normalized severity 0-1")
    time_window: str = Field(description="Human-readable window, e.g. '2024-03-10 14:00-14:15 UTC'")
    source_cluster_id: int

    def to_prompt_block(self) -> str:
        """Render this summary as a structured text block for LLM prompt injection."""
        features_str = ", ".join(
            f"{name} (z={score:.2f})" for name, score in self.top_features
        )
        lines = [
            f"Service: {self.service_name}",
            f"Severity: {self.severity:.2f}",
            f"Window: {self.time_window}",
            f"Top deviating metrics: {features_str}",
        ]
        if self.error_pattern:
            lines.append(f"Error pattern: {self.error_pattern}")
        return "\n".join(lines)


class FaultHypothesis(BaseModel):
    """
    LLM-generated hypothesis mapping an anomaly cluster to a chaos fault.
    This is the schema enforced via instructor at inference time.
    """
    fault_type: FaultType
    target_hint: str = Field(description="Service or component name to target")
    rationale: str = Field(description="Why this fault type matches the observed anomaly")
    confidence: float = Field(ge=0.0, le=1.0, description="Model confidence 0-1")
    source_cluster_id: int
    suggested_duration: str = Field("30s", description="Recommended fault duration")
    suggested_parameters: Dict[str, str] = Field(
        default_factory=dict,
        description="Tool-agnostic fault parameters, e.g. latency=200ms"
    )


class ScenarioComplexityIndex(BaseModel):
    """Quantified complexity score for a ChaosExperiment (SCI)."""
    fault_cardinality: int = Field(description="Number of concurrent fault types")
    target_diversity: int = Field(description="Number of distinct targets")
    temporal_stages: int = Field(1, description="Sequential/parallel phases")
    blast_radius_percent: float = Field(0.0, description="% of system affected")
    causal_chain_depth: int = Field(1, description="Dependency hops tested")
    weighted_score: float = 0.0

    # Default weights -- tunable per evaluation campaign
    WEIGHTS: Dict[str, float] = {
        "fault_cardinality": 0.25,
        "target_diversity": 0.20,
        "temporal_stages": 0.15,
        "blast_radius_percent": 0.20,
        "causal_chain_depth": 0.20,
    }

    def compute(self) -> float:
        raw = (
            self.WEIGHTS["fault_cardinality"] * self.fault_cardinality
            + self.WEIGHTS["target_diversity"] * self.target_diversity
            + self.WEIGHTS["temporal_stages"] * self.temporal_stages
            + self.WEIGHTS["blast_radius_percent"] * (self.blast_radius_percent / 100.0)
            + self.WEIGHTS["causal_chain_depth"] * self.causal_chain_depth
        )
        self.weighted_score = round(raw, 4)
        return self.weighted_score


class AdvisorReport(BaseModel):
    """Output of a full ChaosAdvisor analysis-and-recommend cycle."""
    anomalies_found: int
    clusters: List[AnomalyCluster] = Field(default_factory=list)
    summaries: List[AnomalySummary] = Field(default_factory=list)
    hypotheses: List[FaultHypothesis] = Field(default_factory=list)
    dropped_hypotheses: int = Field(0, description="Hypotheses below confidence threshold")
    generated_experiments: List[ChaosExperiment] = Field(default_factory=list)
    manifest_paths: List[str] = Field(default_factory=list)
    sci_scores: List[ScenarioComplexityIndex] = Field(default_factory=list)
