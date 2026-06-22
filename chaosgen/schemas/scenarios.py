import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from chaosgen.schemas.faults import ChaosExperiment, FaultType
from chaosgen.schemas.incidents import IncidentCandidate


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


# Vague phrases that disqualify a root-cause hypothesis. Matched with word
# boundaries so technical prose ("the timeout could be raised to 5s") is not
# falsely rejected unless the phrase stands as a hedge on its own.
_VAGUE_ROOT_CAUSE_TERMS = (
    "something wrong",
    "maybe",
    "could be",
    "unknown error",
    "not sure",
    "possibly",
    "might be",
)


class ScenarioKnowledgeState(str, Enum):
    """Lifecycle of a scenario along the Unknown -> Known advisor loop."""
    UNKNOWN = "unknown"
    DESCRIBED = "described"
    KNOWN = "known"
    CHAOS_TESTED = "chaos_tested"
    VERIFIED = "verified"


class ExperimentVerdict(str, Enum):
    """Outcome of a verify run against a known scenario's acceptance criteria.

    PARTIAL encodes accepted residual risk: the advisor's `100%?` -> No loop does
    not chase a perfect score, it records the gap and routes back to design.
    """
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"


class UnknownScenarioDescription(BaseModel):
    """
    Structured description of a gatekeeper-confirmed incident (P2).

    Guardrails are enforced here (Field constraints + validators) rather than in
    the prompt alone, so instructor feeds any ValidationError back to the LLM for
    a retry. Separate from FaultHypothesis: this models knowledge refinement, not
    chaos execution.
    """
    title: str = Field(min_length=5)
    root_cause_hypothesis: str = Field(min_length=10)
    repro_steps: List[str] = Field(min_length=2)
    blast_radius_estimate: str = Field(min_length=5)
    suggested_fault_type: FaultType
    confidence: float = Field(ge=0.0, le=1.0)
    source_incident_id: int
    knowledge_state: ScenarioKnowledgeState = ScenarioKnowledgeState.DESCRIBED
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("repro_steps")
    @classmethod
    def repro_steps_non_empty(cls, steps: List[str]) -> List[str]:
        if any(not s.strip() for s in steps):
            raise ValueError("repro_steps must not contain empty strings")
        return steps

    @field_validator("root_cause_hypothesis")
    @classmethod
    def reject_vague_root_cause(cls, value: str) -> str:
        lower = value.lower()
        for term in _VAGUE_ROOT_CAUSE_TERMS:
            if re.search(rf"(?:^|[\s,.]){re.escape(term)}(?:[\s,.]|$)", lower):
                raise ValueError(f"Vague root cause detected: {term!r}")
        return value


class AdvisorReport(BaseModel):
    """Output of a full ChaosAdvisor analysis-and-recommend cycle."""
    anomalies_found: int
    clusters: List[AnomalyCluster] = Field(default_factory=list)
    summaries: List[AnomalySummary] = Field(default_factory=list)
    incident_candidates: List[IncidentCandidate] = Field(
        default_factory=list,
        description="Gatekeeper-evaluated anomaly clusters (P1)",
    )
    filtered_noise_count: int = Field(
        0, description="Number of clusters dropped as NOISE by the gatekeeper (P1)"
    )
    filtered_transient_count: int = Field(
        0, description="Number of TRANSIENT clusters (monitor-only, P1)"
    )
    descriptions: List[UnknownScenarioDescription] = Field(
        default_factory=list,
        description="Structured describe output for REAL/CHRONIC incidents (P2)",
    )
    hypotheses: List[FaultHypothesis] = Field(default_factory=list)
    dropped_hypotheses: int = Field(0, description="Hypotheses below confidence threshold")
    generated_experiments: List[ChaosExperiment] = Field(default_factory=list)
    manifest_paths: List[str] = Field(default_factory=list)
    sci_scores: List[ScenarioComplexityIndex] = Field(default_factory=list)
    run_id: Optional[int] = Field(
        None, description="SQLite analysis_runs.id when history persistence is enabled (P5)"
    )
    description_db_ids: Dict[int, int] = Field(
        default_factory=dict,
        description="source_incident_id → descriptions.id (P5)",
    )
    experiment_db_ids: Dict[str, int] = Field(
        default_factory=dict,
        description="experiment.name → experiments.id (P5)",
    )
