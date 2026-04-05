"""
Scenario Ranker — multi-signal scoring and ranking of chaos scenarios.

Combines LLM confidence, historical experiment value, coverage gap
(recency penalty for repeated fault types), and safety margin into a
composite rank score. Output is fed to the HITL approval view.

Score formula:
    score = (llm_confidence  × 0.35)
           + (historical_value × 0.25)
           + (coverage_gap    × 0.20)
           + (safety_margin   × 0.20)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chaosgen.schemas.faults import ChaosExperiment, FaultType

if TYPE_CHECKING:
    from chaosgen.evaluation.kpi_tracker import KPITracker
    from chaosgen.safety.governance import BlastRadiusController

logger = logging.getLogger(__name__)

_W_CONFIDENCE = 0.35
_W_HISTORICAL = 0.25
_W_COVERAGE = 0.20
_W_SAFETY = 0.20

_RECENCY_WINDOW_SECONDS = 7 * 24 * 3600   # 7 days


@dataclass
class RankedScenario:
    experiment: ChaosExperiment
    rank_score: float
    rank_reason: str
    confidence_score: float = 0.0
    historical_score: float = 0.0
    coverage_score: float = 0.0
    safety_score: float = 0.0
    source: str = "llm"     # "llm" | "catalog"

    def summary(self) -> str:
        return (
            f"[{self.rank_score:.2f}] {self.experiment.name} "
            f"(conf={self.confidence_score:.2f}, hist={self.historical_score:.2f}, "
            f"cov={self.coverage_score:.2f}, safe={self.safety_score:.2f}) "
            f"— {self.rank_reason}"
        )


class ScenarioRanker:
    def __init__(
        self,
        kpi_tracker: "KPITracker | None" = None,
        blast_radius_controller: "BlastRadiusController | None" = None,
    ) -> None:
        self._kpi = kpi_tracker
        self._blast = blast_radius_controller

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rank(
        self,
        experiments: list[ChaosExperiment],
        confidences: dict[str, float] | None = None,
        sources: dict[str, str] | None = None,
        top_n: int | None = None,
    ) -> list[RankedScenario]:
        """
        Score and sort experiments. Returns RankedScenario list (highest first).

        Args:
            experiments: ChaosExperiment list to rank.
            confidences: Map of experiment.name → LLM confidence (0-1).
                         Missing entries default to 0.6.
            sources: Map of experiment.name → "llm" | "catalog".
            top_n: If set, return only the top N ranked scenarios.
        """
        confidences = confidences or {}
        sources = sources or {}

        # Compute coverage gap: which fault types have been tested recently?
        recent_fault_types = self._get_recent_fault_types()

        ranked: list[RankedScenario] = []
        for exp in experiments:
            rs = self._score(exp, confidences, recent_fault_types, sources)
            ranked.append(rs)

        ranked.sort(key=lambda r: r.rank_score, reverse=True)

        if top_n is not None:
            ranked = ranked[:top_n]

        logger.info(
            "Ranked %d scenarios (top: %s @ %.2f)",
            len(ranked),
            ranked[0].experiment.name if ranked else "none",
            ranked[0].rank_score if ranked else 0.0,
        )
        return ranked

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(
        self,
        experiment: ChaosExperiment,
        confidences: dict[str, float],
        recent_fault_types: set[FaultType],
        sources: dict[str, str],
    ) -> RankedScenario:
        confidence_score = self._compute_confidence(experiment, confidences)
        historical_score = self._compute_historical(experiment)
        coverage_score = self._compute_coverage_gap(experiment, recent_fault_types)
        safety_score = self._compute_safety_margin(experiment)

        total = (
            confidence_score  * _W_CONFIDENCE
            + historical_score * _W_HISTORICAL
            + coverage_score   * _W_COVERAGE
            + safety_score     * _W_SAFETY
        )

        reasons: list[str] = []
        if confidence_score >= 0.8:
            reasons.append("high LLM confidence")
        if historical_score >= 0.7:
            reasons.append("historically valuable")
        if coverage_score >= 0.8:
            reasons.append("untested fault type")
        if safety_score >= 0.9:
            reasons.append("well within blast radius")

        return RankedScenario(
            experiment=experiment,
            rank_score=round(total, 4),
            rank_reason="; ".join(reasons) if reasons else "standard scenario",
            confidence_score=round(confidence_score, 3),
            historical_score=round(historical_score, 3),
            coverage_score=round(coverage_score, 3),
            safety_score=round(safety_score, 3),
            source=sources.get(experiment.name, "llm"),
        )

    def _compute_confidence(
        self,
        experiment: ChaosExperiment,
        confidences: dict[str, float],
    ) -> float:
        """LLM confidence or 0.6 default for catalog entries (no LLM score)."""
        return min(max(confidences.get(experiment.name, 0.6), 0.0), 1.0)

    def _compute_historical(self, experiment: ChaosExperiment) -> float:
        """
        Score based on past actionability: did previous runs of similar
        scenarios lead to identified bugs or improvements?
        Returns 0.5 (neutral) when KPITracker is unavailable.
        """
        if self._kpi is None:
            return 0.5

        try:
            report = self._kpi.generate_report()
            # Use the actionability rate as a proxy for historical value
            actionability = report.get("actionability_rate", 0.5)
            return min(max(float(actionability), 0.0), 1.0)
        except Exception as exc:
            logger.debug("KPITracker historical score failed: %s", exc)
            return 0.5

    def _compute_coverage_gap(
        self,
        experiment: ChaosExperiment,
        recent_fault_types: set[FaultType],
    ) -> float:
        """
        High score = fault type NOT recently tested (coverage gap exists).
        Low score  = fault type was tested recently (diminishing returns).
        """
        exp_fault_types = {f.fault_type for f in experiment.faults}
        overlap = exp_fault_types & recent_fault_types
        if not overlap:
            return 1.0          # entirely untested fault types
        if len(overlap) == len(exp_fault_types):
            return 0.2          # all fault types recently covered
        return 0.6              # partial overlap

    def _compute_safety_margin(self, experiment: ChaosExperiment) -> float:
        """
        High score = well within blast radius limits.
        Returns 1.0 (fully safe) when BlastRadiusController is unavailable.
        """
        if self._blast is None:
            return 1.0
        try:
            self._blast.validate(experiment)
            return 1.0          # passed validation — full safety margin
        except ValueError:
            return 0.0          # would violate safety policy
        except Exception:
            return 0.5

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_recent_fault_types(self) -> set[FaultType]:
        """
        Return fault types from experiments run within the recency window.
        Falls back to empty set when KPITracker unavailable.
        """
        if self._kpi is None:
            return set()
        try:
            results = getattr(self._kpi, "_results", [])
            cutoff = time.time() - _RECENCY_WINDOW_SECONDS
            recent: set[FaultType] = set()
            for r in results:
                ts = getattr(r, "timestamp", 0)
                if ts >= cutoff:
                    exp = getattr(r, "experiment", None)
                    if exp:
                        for fault in getattr(exp, "faults", []):
                            recent.add(fault.fault_type)
            return recent
        except Exception as exc:
            logger.debug("Could not retrieve recent fault types: %s", exc)
            return set()
