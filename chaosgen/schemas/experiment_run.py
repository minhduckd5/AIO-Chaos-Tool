"""Experiment run record for anomaly retrain / verdict alignment."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from chaosgen.schemas.scenarios import ExperimentVerdict


class WindowResolutionSource(str, Enum):
    """How the telemetry fetch window was derived."""

    JOURNAL = "journal"
    JOURNAL_START_ESTIMATE = "journal_start_estimate"
    MTIME_ESTIMATE = "mtime_estimate"


class ExperimentRunRecord(BaseModel):
    """One CTK experiment run joined to verdict labels and telemetry window."""

    run_id: str
    experiment_name: str
    window_start: datetime
    window_end: datetime
    window_padding_seconds: int = 60
    window_source: WindowResolutionSource
    verdict: Optional[ExperimentVerdict] = None
    ctk_status: Optional[str] = None
    deviated: Optional[bool] = None
    aborted: bool = False
    dry_run: bool = False
    journal_path: Optional[str] = None
    experiment_json_path: Optional[str] = None
    verdict_path: Optional[str] = None
    telemetry_cache_path: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
