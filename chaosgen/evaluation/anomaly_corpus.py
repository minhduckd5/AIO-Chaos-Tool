"""
Merge baseline exports with cached CTK telemetry windows for IF retrain.

Contamination policy keeps baseline healthy rows dominant; inject windows are
capped and contamination is adjusted so failed chaos runs do not swamp the
decision boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Sequence, Union

import pandas as pd

from chaosgen.config.settings import ChaosGenSettings, load_settings
from chaosgen.evaluation.run_telemetry import (
    DEFAULT_RUN_TELEMETRY_DIR,
    load_cached_run_telemetry,
)
from chaosgen.evaluation.verdict_alignment import load_cached_runs_from_dir
from chaosgen.ingestion.export_loader import ExportLoader
from chaosgen.ml.canonical_features import apply_canonical_features
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.schemas.scenarios import ExperimentVerdict
from chaosgen.schemas.telemetry import TelemetryDataset

logger = logging.getLogger(__name__)

MIN_CONTAMINATION = 0.01


class ContaminationMode(str, Enum):
    """How to set IsolationForest contamination for retrain."""

    BASELINE_ONLY = "baseline_only"
    PROPORTIONAL = "proportional"
    FIXED = "fixed"


class CtkVerdictFilter(str, Enum):
    """Which cached CTK runs may contribute rows to the merged corpus."""

    NONE = "none"
    PASS_ONLY = "pass_only"
    ALL = "all"


@dataclass
class CorpusBuildResult:
    features: pd.DataFrame
    baseline_rows: int
    ctk_rows_requested: int
    ctk_rows_used: int
    contamination: float
    contamination_mode: ContaminationMode
    notes: List[str]


def _features_from_dataset(
    dataset: TelemetryDataset,
    fe: FeatureEngineer,
    settings: ChaosGenSettings,
) -> pd.DataFrame:
    features = fe.transform(dataset)
    if features.empty:
        return features
    return apply_canonical_features(features, settings.features)


def load_baseline_features(
    export_paths: Sequence[Union[str, Path]],
    settings: ChaosGenSettings,
) -> pd.DataFrame:
    fe = FeatureEngineer(settings=settings.features)
    frames: List[pd.DataFrame] = []
    for path in export_paths:
        loader = ExportLoader.resolve_bundle(path)
        dataset = loader.load()
        features = _features_from_dataset(dataset, fe, settings)
        if not features.empty:
            frames.append(features)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, axis=0, sort=True)
    combined = combined.fillna(0.0)
    combined = combined[~combined.index.duplicated(keep="first")]
    return combined.sort_index()


def load_ctk_run_features(
    runs_dir: Union[str, Path],
    settings: ChaosGenSettings,
    *,
    verdict_filter: CtkVerdictFilter = CtkVerdictFilter.PASS_ONLY,
) -> tuple[pd.DataFrame, int]:
    """Load feature rows from cached per-run telemetry bundles."""
    fe = FeatureEngineer(settings=settings.features)
    cached = load_cached_runs_from_dir(runs_dir)
    frames: List[pd.DataFrame] = []
    skipped = 0

    for record, dataset in cached:
        if verdict_filter == CtkVerdictFilter.NONE:
            skipped += 1
            continue
        if verdict_filter == CtkVerdictFilter.PASS_ONLY:
            if record.verdict is not None and record.verdict != ExperimentVerdict.PASS:
                skipped += 1
                continue
            if record.aborted or record.dry_run:
                skipped += 1
                continue

        features = _features_from_dataset(dataset, fe, settings)
        if features.empty:
            skipped += 1
            continue
        frames.append(features)

    if not frames:
        return pd.DataFrame(), skipped

    combined = pd.concat(frames, axis=0, sort=True)
    combined = combined.fillna(0.0)
    combined = combined[~combined.index.duplicated(keep="first")]
    return combined.sort_index(), skipped


def compute_fit_contamination(
    baseline_rows: int,
    ctk_rows: int,
    base_contamination: float,
    *,
    mode: ContaminationMode,
    max_ctk_fraction: float,
    fixed_contamination: Optional[float] = None,
) -> tuple[float, int]:
    """
    Return (contamination, capped_ctk_rows).

    ``proportional`` assumes natural anomalies live only in baseline noise at
  ``base_contamination``; adding inject windows lowers contamination so IF
    does not treat the whole merged set as equally noisy.
    """
    if baseline_rows <= 0:
        raise ValueError("baseline_rows must be positive")

    base_contamination = float(base_contamination)
    max_ctk_fraction = max(0.0, float(max_ctk_fraction))

    if ctk_rows <= 0 or mode == ContaminationMode.BASELINE_ONLY:
        if mode == ContaminationMode.FIXED and fixed_contamination is not None:
            return max(MIN_CONTAMINATION, min(fixed_contamination, 0.49)), 0
        return max(MIN_CONTAMINATION, min(base_contamination, 0.49)), 0

    max_ctk_rows = int(baseline_rows * max_ctk_fraction)
    capped_ctk = min(ctk_rows, max_ctk_rows)
    total = baseline_rows + capped_ctk

    if mode == ContaminationMode.FIXED:
        value = fixed_contamination if fixed_contamination is not None else base_contamination
        return max(MIN_CONTAMINATION, min(value, 0.49)), capped_ctk

    expected_anomalies = baseline_rows * base_contamination
    proportional = expected_anomalies / total
    effective = min(base_contamination, proportional)
    return max(MIN_CONTAMINATION, effective), capped_ctk


def _align_and_merge(
    baseline: pd.DataFrame,
    ctk: pd.DataFrame,
    capped_ctk_rows: int,
) -> pd.DataFrame:
    if baseline.empty:
        raise ValueError("baseline feature matrix is empty")

    if ctk.empty or capped_ctk_rows <= 0:
        return baseline

    ctk_part = ctk.tail(capped_ctk_rows) if len(ctk) > capped_ctk_rows else ctk
    merged = pd.concat([baseline, ctk_part], axis=0, sort=True)
    merged = merged.fillna(0.0)
    merged = merged[~merged.index.duplicated(keep="first")]
    return merged.sort_index()


def build_retrain_corpus(
    export_paths: Sequence[Union[str, Path]],
    settings: ChaosGenSettings,
    *,
    include_ctk_runs_dir: Optional[Union[str, Path]] = None,
    verdict_filter: CtkVerdictFilter = CtkVerdictFilter.PASS_ONLY,
    contamination_mode: ContaminationMode = ContaminationMode.PROPORTIONAL,
    max_ctk_fraction: float = 0.10,
    fixed_contamination: Optional[float] = None,
) -> CorpusBuildResult:
    """Merge baseline exports with optional capped CTK windows."""
    notes: List[str] = []
    baseline = load_baseline_features(export_paths, settings)
    if baseline.empty:
        raise ValueError("no baseline features from export paths")

    baseline_rows = len(baseline)
    ctk = pd.DataFrame()
    ctk_requested = 0

    if include_ctk_runs_dir and contamination_mode != ContaminationMode.BASELINE_ONLY:
        ctk, skipped = load_ctk_run_features(
            include_ctk_runs_dir,
            settings,
            verdict_filter=verdict_filter,
        )
        ctk_requested = len(ctk)
        if skipped:
            notes.append(f"skipped {skipped} cached runs (verdict/filter/empty)")
    elif include_ctk_runs_dir and contamination_mode == ContaminationMode.BASELINE_ONLY:
        notes.append("CTK runs ignored: contamination_mode=baseline_only")

    base_cont = settings.anomaly.contamination
    contamination, capped_ctk = compute_fit_contamination(
        baseline_rows,
        ctk_requested,
        base_cont,
        mode=contamination_mode,
        max_ctk_fraction=max_ctk_fraction,
        fixed_contamination=fixed_contamination,
    )

    if capped_ctk < ctk_requested:
        notes.append(
            f"CTK rows capped {ctk_requested} -> {capped_ctk} "
            f"(max_ctk_fraction={max_ctk_fraction})"
        )

    if contamination < base_cont and ctk_requested > 0:
        notes.append(
            f"contamination reduced {base_cont:.4f} -> {contamination:.4f} (proportional)"
        )

    features = _align_and_merge(baseline, ctk, capped_ctk)

    return CorpusBuildResult(
        features=features,
        baseline_rows=baseline_rows,
        ctk_rows_requested=ctk_requested,
        ctk_rows_used=capped_ctk,
        contamination=contamination,
        contamination_mode=contamination_mode,
        notes=notes,
    )


def default_runs_dir() -> Path:
    return DEFAULT_RUN_TELEMETRY_DIR
