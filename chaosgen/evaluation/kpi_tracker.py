import csv
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExperimentRecord:
    """Single experiment evaluation record."""
    experiment_name: str
    source: str  # "ai" or "human"
    accepted: bool
    modifications: int
    new_failures_found: int
    design_time_minutes: float
    sci_score: float = 0.0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(tz=timezone.utc).isoformat()


class KPITracker:
    """
    Tracks thesis KPIs: Actionability Rate, Discovery Rate, Time-to-Design.
    Supports JSON/CSV export for thesis data tables.
    """

    def __init__(self):
        self.records: List[ExperimentRecord] = []

    def record_experiment_result(
        self,
        experiment_name: str,
        source: str = "ai",
        accepted: bool = True,
        modifications: int = 0,
        new_failures_found: int = 0,
        design_time_minutes: float = 0.0,
        sci_score: float = 0.0,
    ) -> None:
        record = ExperimentRecord(
            experiment_name=experiment_name,
            source=source,
            accepted=accepted,
            modifications=modifications,
            new_failures_found=new_failures_found,
            design_time_minutes=design_time_minutes,
            sci_score=sci_score,
        )
        self.records.append(record)
        logger.info("Recorded experiment: %s (source=%s, accepted=%s)", 
                     experiment_name, source, accepted)

    def compute_actionability_rate(self, source: str = "ai") -> float:
        """% of AI-generated scenarios accepted without modification."""
        filtered = [r for r in self.records if r.source == source]
        if not filtered:
            return 0.0
        accepted_clean = sum(1 for r in filtered if r.accepted and r.modifications == 0)
        return round(accepted_clean / len(filtered) * 100, 2)

    def compute_discovery_rate(self, source: str = "ai") -> float:
        """Average new failure modes discovered per experiment."""
        filtered = [r for r in self.records if r.source == source]
        if not filtered:
            return 0.0
        total_discoveries = sum(r.new_failures_found for r in filtered)
        return round(total_discoveries / len(filtered), 2)

    def compute_time_to_design(self, source: str = "ai") -> float:
        """Average design time in minutes."""
        filtered = [r for r in self.records if r.source == source and r.design_time_minutes > 0]
        if not filtered:
            return 0.0
        return round(sum(r.design_time_minutes for r in filtered) / len(filtered), 2)

    def compute_all_kpis(self) -> Dict[str, Dict[str, float]]:
        """Compute all KPIs for both AI and human sources."""
        result = {}
        for source in ("ai", "human"):
            result[source] = {
                "actionability_rate": self.compute_actionability_rate(source),
                "discovery_rate": self.compute_discovery_rate(source),
                "time_to_design_minutes": self.compute_time_to_design(source),
                "total_experiments": len([r for r in self.records if r.source == source]),
            }
        return result

    def export_report(self, path: str, format: str = "json") -> None:
        """Export all records and KPIs to file."""
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "json":
            data = {
                "kpis": self.compute_all_kpis(),
                "records": [asdict(r) for r in self.records],
            }
            output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        elif format == "csv":
            if not self.records:
                return
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=asdict(self.records[0]).keys())
                writer.writeheader()
                for r in self.records:
                    writer.writerow(asdict(r))
        else:
            raise ValueError(f"Unsupported format: {format}")

        logger.info("KPI report exported to %s (%s)", output_path, format)
