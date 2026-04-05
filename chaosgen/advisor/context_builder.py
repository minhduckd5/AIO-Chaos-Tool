"""
Context Builder — assembles ScenarioContext from DiscoveryReport + anomalies + SafetyPolicy.

ScenarioContext is the single object injected into every LLM prompt.
It is a pure Pydantic model — no raw NetworkX graphs, no unserializable objects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from chaosgen.schemas.discovery import (
    ArchitectureType,
    DiscoveryReport,
    DiscoverySignal,
    EnvironmentProfile,
    ObservabilityProfile,
    ProbeOutcome,
    ServiceMap,
)
from chaosgen.schemas.scenarios import AnomalySummary
from chaosgen.schemas.faults import ChaosExperiment

if TYPE_CHECKING:
    from chaosgen.safety.governance import SafetyPolicy

logger = logging.getLogger(__name__)

_MAX_PREVIOUS_EXPERIMENTS = 5   # cap to keep LLM prompt size manageable
_MAX_ANOMALIES = 10


class ScenarioContext(BaseModel):
    """
    Aggregated context fed into LLM scenario generation prompts.
    All fields are JSON-serializable (Pydantic models only).
    """

    architecture_type: ArchitectureType
    service_map: ServiceMap
    environment: EnvironmentProfile
    anomalies: list[AnomalySummary] = Field(default_factory=list)
    observability: ObservabilityProfile
    discovery_signals: list[DiscoverySignal] = Field(default_factory=list)
    previous_experiment_names: list[str] = Field(
        default_factory=list,
        description="Names of recently run experiments — used to avoid redundant suggestions",
    )
    safety_summary: str = ""

    def to_prompt_text(self) -> str:
        """
        Render context to a compact, token-efficient string suitable for
        injection into an LLM system or user prompt.
        """
        lines = [
            "=== SYSTEM CONTEXT ===",
            f"Architecture: {self.architecture_type.value}",
            f"Environment: {self.environment.type.value}"
            + (f" (cloud={self.environment.cloud_provider})" if self.environment.cloud_provider else ""),
            f"Services: {len(self.service_map.nodes)} nodes, {len(self.service_map.edges)} edges",
            f"Observability: metrics={self.observability.has_metrics}, "
            f"logs={self.observability.has_logs}, traces={self.observability.has_traces}",
        ]

        if self.service_map.critical_paths:
            paths_str = " | ".join(" → ".join(p) for p in self.service_map.critical_paths[:3])
            lines.append(f"Critical paths: {paths_str}")

        if self.safety_summary:
            lines.append(f"Safety constraints: {self.safety_summary}")

        mismatch = [s for s in self.discovery_signals if s.flag == "USER_HEURISTIC_MISMATCH"]
        if mismatch:
            lines.append("\n=== CONFIGURATION WARNINGS ===")
            for s in mismatch:
                lines.append(f"  WARNING: {s.key}: {s.message}")

        auth_issues = [
            s for s in self.discovery_signals
            if s.probe_outcome == ProbeOutcome.AUTH_REJECTED
        ]
        if auth_issues:
            lines.append("\n=== AUTH ISSUES (tools exist but credentials rejected) ===")
            for s in auth_issues:
                lines.append(f"  {s.key}: {s.message}")

        if self.anomalies:
            lines.append(f"\n=== DETECTED ANOMALIES ({len(self.anomalies)}) ===")
            for i, anomaly in enumerate(self.anomalies[:_MAX_ANOMALIES], 1):
                lines.append(
                    f"[{i}] service={anomaly.service_name} "
                    f"severity={anomaly.severity:.2f} "
                    f"window={anomaly.time_window}"
                )

        if self.previous_experiment_names:
            lines.append(f"\n=== RECENTLY TESTED (avoid repeating) ===")
            lines.extend(f"- {name}" for name in self.previous_experiment_names)

        return "\n".join(lines)


class ContextBuilder:
    """Assembles a ScenarioContext from all available inputs."""

    def __init__(self, discovery_report: DiscoveryReport) -> None:
        self._report = discovery_report

    def build(
        self,
        anomalies: list[AnomalySummary] | None = None,
        previous_experiments: list[ChaosExperiment] | None = None,
        safety_policy: "SafetyPolicy | None" = None,
    ) -> ScenarioContext:
        """
        Build and return a ScenarioContext.

        Args:
            anomalies: AnomalySummary list from AnomalyDetector. Optional.
            previous_experiments: Recently executed experiments for deduplication.
            safety_policy: Current safety policy to summarise for the LLM.
        """
        safety_summary = self._format_safety_policy(safety_policy)

        prev_names = [
            getattr(exp, "name", str(exp))
            for exp in (previous_experiments or [])[:_MAX_PREVIOUS_EXPERIMENTS]
        ]

        ctx = ScenarioContext(
            architecture_type=self._report.architecture.type,
            service_map=self._report.service_map,
            environment=self._report.environment,
            anomalies=(anomalies or [])[:_MAX_ANOMALIES],
            observability=self._report.observability,
            discovery_signals=self._report.signals,
            previous_experiment_names=prev_names,
            safety_summary=safety_summary,
        )

        logger.debug(
            "ScenarioContext built: arch=%s env=%s services=%d anomalies=%d",
            ctx.architecture_type.value,
            ctx.environment.type.value,
            len(ctx.service_map.nodes),
            len(ctx.anomalies),
        )

        return ctx

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_safety_policy(policy: "SafetyPolicy | None") -> str:
        if policy is None:
            return ""
        parts = []
        if hasattr(policy, "max_blast_radius_pods_pct"):
            parts.append(f"max_blast_radius={policy.max_blast_radius_pods_pct}% pods")
        if hasattr(policy, "blocked_namespaces") and policy.blocked_namespaces:
            parts.append(f"blocked_namespaces={list(policy.blocked_namespaces)}")
        if hasattr(policy, "blocked_services") and policy.blocked_services:
            parts.append(f"blocked_services={list(policy.blocked_services)}")
        return "; ".join(parts)
