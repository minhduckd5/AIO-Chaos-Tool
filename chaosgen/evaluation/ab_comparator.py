import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from chaosgen.schemas.faults import ChaosExperiment
from chaosgen.schemas.scenarios import ScenarioComplexityIndex
from chaosgen.advisor.scenario_generator import ScenarioGenerator
from chaosgen.evaluation.kpi_tracker import KPITracker

logger = logging.getLogger(__name__)


@dataclass
class ComparisonReport:
    """Side-by-side KPI comparison between AI and human scenarios."""
    human_kpis: Dict[str, float] = field(default_factory=dict)
    ai_kpis: Dict[str, float] = field(default_factory=dict)
    human_sci_stats: Dict[str, float] = field(default_factory=dict)
    ai_sci_stats: Dict[str, float] = field(default_factory=dict)
    discovery_delta: float = 0.0
    time_savings_percent: float = 0.0
    actionability_comparison: str = ""


class ABComparator:
    """
    A/B comparison framework for evaluating AI-generated vs
    human-authored chaos scenarios.
    """

    def __init__(self, kpi_tracker: Optional[KPITracker] = None):
        self.kpi_tracker = kpi_tracker or KPITracker()
        self.human_scenarios: List[ChaosExperiment] = []
        self.ai_scenarios: List[ChaosExperiment] = []

    def register_human_scenarios(self, scenarios: List[ChaosExperiment]) -> None:
        self.human_scenarios.extend(scenarios)
        logger.info("Registered %d human scenarios (total: %d)", 
                     len(scenarios), len(self.human_scenarios))

    def register_ai_scenarios(self, scenarios: List[ChaosExperiment]) -> None:
        self.ai_scenarios.extend(scenarios)
        logger.info("Registered %d AI scenarios (total: %d)", 
                     len(scenarios), len(self.ai_scenarios))

    def run_comparison(self) -> ComparisonReport:
        """Generate a comparison report from recorded KPIs and SCI scores."""
        all_kpis = self.kpi_tracker.compute_all_kpis()

        human_scis = [ScenarioGenerator.compute_sci(exp) for exp in self.human_scenarios]
        ai_scis = [ScenarioGenerator.compute_sci(exp) for exp in self.ai_scenarios]

        for sci in human_scis + ai_scis:
            sci.compute()

        human_sci_stats = self._compute_sci_stats(human_scis)
        ai_sci_stats = self._compute_sci_stats(ai_scis)

        human_kpis = all_kpis.get("human", {})
        ai_kpis = all_kpis.get("ai", {})

        discovery_delta = (
            ai_kpis.get("discovery_rate", 0) - human_kpis.get("discovery_rate", 0)
        )

        human_time = human_kpis.get("time_to_design_minutes", 0)
        ai_time = ai_kpis.get("time_to_design_minutes", 0)
        time_savings = (
            ((human_time - ai_time) / human_time * 100) if human_time > 0 else 0.0
        )

        ai_action = ai_kpis.get("actionability_rate", 0)
        comparison_str = (
            f"AI actionability: {ai_action:.1f}% | "
            f"AI discovers {discovery_delta:+.2f} more failures/experiment | "
            f"Time savings: {time_savings:.1f}%"
        )

        report = ComparisonReport(
            human_kpis=human_kpis,
            ai_kpis=ai_kpis,
            human_sci_stats=human_sci_stats,
            ai_sci_stats=ai_sci_stats,
            discovery_delta=round(discovery_delta, 4),
            time_savings_percent=round(time_savings, 2),
            actionability_comparison=comparison_str,
        )

        logger.info("Comparison report: %s", report.actionability_comparison)
        return report

    def export_report(self, path: str) -> None:
        """Export the comparison report to JSON."""
        report = self.run_comparison()
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(asdict(report), indent=2), encoding="utf-8"
        )
        logger.info("A/B comparison report exported to %s", output_path)

    @staticmethod
    def _compute_sci_stats(
        sci_list: List[ScenarioComplexityIndex],
    ) -> Dict[str, float]:
        """Compute statistical summary of SCI scores."""
        if not sci_list:
            return {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}

        scores = [s.weighted_score for s in sci_list]
        n = len(scores)
        mean = sum(scores) / n
        variance = sum((x - mean) ** 2 for x in scores) / n
        std = variance ** 0.5

        return {
            "count": n,
            "mean": round(mean, 4),
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
            "std": round(std, 4),
        }
