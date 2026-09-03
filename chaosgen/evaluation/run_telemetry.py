"""Fetch and cache per-run telemetry windows for anomaly retrain."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Union

from chaosgen.config.settings import ChaosGenSettings, load_settings
from chaosgen.ingestion.collector import TelemetryCollector
from chaosgen.ingestion.telemetry_factory import build_telemetry_collector
from chaosgen.schemas.experiment_run import ExperimentRunRecord
from chaosgen.schemas.telemetry import TelemetryDataset

logger = logging.getLogger(__name__)

RUN_TELEMETRY_SCHEMA_VERSION = "1"
DEFAULT_RUN_TELEMETRY_DIR = Path("scratch/telemetry/runs")


def default_cache_path(output_dir: Path, run_id: str) -> Path:
    return output_dir / f"{run_id}.json"


def cache_run_telemetry(
    record: ExperimentRunRecord,
    collector: TelemetryCollector,
    *,
    output_dir: Union[str, Path] = DEFAULT_RUN_TELEMETRY_DIR,
    step: Optional[str] = None,
    settings: Optional[ChaosGenSettings] = None,
) -> ExperimentRunRecord:
    """Query Prometheus/Loki for the run window and write a JSON cache bundle."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    resolved_step = step
    if resolved_step is None:
        if settings is not None:
            resolved_step = settings.telemetry.step
        else:
            resolved_step = load_settings().telemetry.step

    logger.info(
        "Fetching telemetry for %s (%s -> %s)",
        record.run_id,
        record.window_start.isoformat(),
        record.window_end.isoformat(),
    )

    dataset = collector.collect_range(
        record.window_start,
        record.window_end,
        step=resolved_step,
    )

    cache_path = default_cache_path(out, record.run_id)
    payload = {
        "schema_version": RUN_TELEMETRY_SCHEMA_VERSION,
        "run_record": record.model_dump(mode="json"),
        "telemetry": dataset.model_dump(mode="json"),
    }
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "Cached telemetry: %s (%d series, %d samples)",
        cache_path,
        len(dataset.metrics),
        dataset.total_samples,
    )

    return record.model_copy(update={"telemetry_cache_path": str(cache_path.resolve())})


def load_cached_run_telemetry(path: Union[str, Path]) -> tuple[ExperimentRunRecord, TelemetryDataset]:
    """Load a prior cache bundle written by ``cache_run_telemetry``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    record = ExperimentRunRecord.model_validate(data["run_record"])
    dataset = TelemetryDataset.model_validate(data["telemetry"])
    return record, dataset


def fetch_and_cache_run(
    record: ExperimentRunRecord,
    *,
    output_dir: Union[str, Path] = DEFAULT_RUN_TELEMETRY_DIR,
    config_path: Optional[str] = None,
    step: Optional[str] = None,
    prom_url: Optional[str] = None,
    loki_url: Optional[str] = None,
) -> ExperimentRunRecord:
    """Build live collector from settings and cache telemetry for one run."""
    settings = load_settings(config_path)
    collector = build_telemetry_collector(
        settings,
        prom_url=prom_url,
        loki_url=loki_url,
    )
    return cache_run_telemetry(
        record,
        collector,
        output_dir=output_dir,
        step=step,
        settings=settings,
    )
