"""
Verdict ↔ Isolation Forest alignment evaluation for thesis / demo.

Scores cached per-run telemetry windows with a baseline-trained IF model and
reports whether unsupervised spikes align with operational verdict labels.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from chaosgen.config.settings import ChaosGenSettings, load_settings
from chaosgen.evaluation.run_catalog import build_run_records
from chaosgen.evaluation.run_telemetry import (
    DEFAULT_RUN_TELEMETRY_DIR,
    load_cached_run_telemetry,
)
from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.canonical_features import apply_canonical_features
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.schemas.experiment_run import ExperimentRunRecord
from chaosgen.schemas.scenarios import AnomalySeverity, ExperimentVerdict
from chaosgen.schemas.telemetry import TelemetryDataset

logger = logging.getLogger(__name__)

SEVERITY_NUMERIC: Dict[AnomalySeverity, float] = {
    AnomalySeverity.LOW: 0.25,
    AnomalySeverity.MEDIUM: 0.50,
    AnomalySeverity.HIGH: 0.75,
    AnomalySeverity.CRITICAL: 1.0,
}


class AlignmentOutcome(str, Enum):
    ALIGNED = "aligned"
    MISSED_ANOMALY = "missed_anomaly"
    FALSE_ALARM = "false_alarm"
    PARTIAL_MATCH = "partial_match"
    EXCLUDED = "excluded"


class WindowAnomalyScore(BaseModel):
    feature_rows: int = 0
    anomaly_count: int = 0
    anomaly_fraction: float = 0.0
    max_severity: Optional[str] = None
    max_severity_score: float = 0.0
    any_spike: bool = False
    mean_outlier_score: float = 0.0
    warnings: List[str] = Field(default_factory=list)


class RunAlignmentResult(BaseModel):
    run_id: str
    experiment_name: str
    verdict: Optional[str] = None
    ctk_status: Optional[str] = None
    aborted: bool = False
    dry_run: bool = False
    window_start: str
    window_end: str
    journal_path: Optional[str] = None
    telemetry_cache_path: Optional[str] = None
    telemetry_series: int = 0
    telemetry_samples: int = 0
    window_score: WindowAnomalyScore
    alignment_outcome: AlignmentOutcome
    alignment_note: str = ""


class ConfusionCell(BaseModel):
    verdict: str
    if_spike: bool
    count: int


class AlignmentSummary(BaseModel):
    evaluable_runs: int = 0
    excluded_runs: int = 0
    aligned_runs: int = 0
    alignment_rate: float = 0.0
    fail_runs: int = 0
    fail_with_spike: int = 0
    fail_detection_rate: float = 0.0
    pass_runs: int = 0
    pass_without_spike: int = 0
    pass_specificity: float = 0.0
    partial_runs: int = 0
    partial_with_spike: int = 0


class AlignmentReport(BaseModel):
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model_path: Optional[str] = None
    runs_dir: Optional[str] = None
    summary: AlignmentSummary
    confusion_matrix: List[ConfusionCell] = Field(default_factory=list)
    runs: List[RunAlignmentResult] = Field(default_factory=list)
    narrative: str = ""


def _resolve_model_path(
    model_path: Optional[str],
    settings: ChaosGenSettings,
) -> Optional[Path]:
    resolved = model_path or settings.anomaly.default_model_path
    if not resolved:
        return None
    path = Path(resolved)
    return path if path.is_file() else None


def _build_detector(
    settings: ChaosGenSettings,
    model_path: Optional[str],
) -> tuple[AnomalyDetector, Optional[Path], List[str]]:
    warnings: List[str] = []
    detector = AnomalyDetector(settings=settings.anomaly)
    resolved = _resolve_model_path(model_path, settings)
    if resolved:
        detector.load_model(str(resolved))
        return detector, resolved, warnings

    warnings.append(
        "no pre-trained model found; scoring will refit IF on each window (not thesis-grade)"
    )
    return detector, None, warnings


def score_features_window(
    detector: AnomalyDetector,
    features: pd.DataFrame,
    *,
    refit_if_unfitted: bool = True,
) -> WindowAnomalyScore:
    """Score a feature matrix with IsolationForest labels and severity."""
    warnings: List[str] = []
    if features.empty:
        warnings.append("empty feature matrix")
        return WindowAnomalyScore(warnings=warnings)

    work = features
    if detector._is_fitted and detector._feature_names:
        work = detector._align_for_inference(features)

    if not detector._is_fitted:
        if not refit_if_unfitted:
            warnings.append("detector not fitted")
            return WindowAnomalyScore(feature_rows=len(features), warnings=warnings)
        detector.fit(work)
        warnings.append("detector fitted on evaluation window (unsupervised fallback)")

    expected = getattr(detector.scaler, "n_features_in_", None)
    if expected is not None and work.shape[1] != expected:
        warnings.append(
            f"feature mismatch: model expects {expected}, got {work.shape[1]}; refitting on window"
        )
        detector.fit(work)

    scaled = detector.scaler.transform(work.values)
    labels = detector.iso_forest.predict(scaled)
    scores = detector.iso_forest.decision_function(scaled)

    anomaly_mask = labels == -1
    anomaly_count = int(anomaly_mask.sum())
    total = len(labels)
    anomaly_fraction = float(anomaly_count / total) if total else 0.0
    any_spike = anomaly_count > 0

    if anomaly_count > 0:
        worst_score = float(np.min(scores[anomaly_mask]))
        severity = detector._score_to_severity(worst_score)
        max_severity_score = SEVERITY_NUMERIC.get(severity, 0.5)
    else:
        worst_score = float(np.max(scores)) if len(scores) else 0.0
        severity = AnomalySeverity.LOW
        max_severity_score = 0.0

    raw = -scores
    mean_outlier = float(np.mean(raw)) if len(raw) else 0.0

    return WindowAnomalyScore(
        feature_rows=total,
        anomaly_count=anomaly_count,
        anomaly_fraction=round(anomaly_fraction, 4),
        max_severity=severity.value,
        max_severity_score=round(max_severity_score, 4),
        any_spike=any_spike,
        mean_outlier_score=round(mean_outlier, 4),
        warnings=warnings,
    )


def features_from_dataset(
    dataset: TelemetryDataset,
    settings: ChaosGenSettings,
) -> pd.DataFrame:
    fe = FeatureEngineer(settings=settings.features)
    features = fe.transform(dataset)
    if features.empty:
        return features
    return apply_canonical_features(features, settings.features)


def classify_alignment(
    verdict: Optional[ExperimentVerdict],
    any_spike: bool,
    *,
    aborted: bool = False,
    dry_run: bool = False,
    feature_rows: Optional[int] = None,
) -> tuple[AlignmentOutcome, str]:
    if aborted or dry_run:
        return AlignmentOutcome.EXCLUDED, "aborted or dry-run experiment"
    if feature_rows is not None and feature_rows == 0:
        return AlignmentOutcome.EXCLUDED, "no feature rows (empty telemetry)"
    if verdict is None:
        return AlignmentOutcome.EXCLUDED, "no verdict label"

    if verdict == ExperimentVerdict.FAIL:
        if any_spike:
            return AlignmentOutcome.ALIGNED, "FAIL verdict with IF spike in chaos window"
        return AlignmentOutcome.MISSED_ANOMALY, "FAIL verdict but IF saw no spike"

    if verdict == ExperimentVerdict.PASS:
        if any_spike:
            return AlignmentOutcome.FALSE_ALARM, "PASS verdict but IF flagged spike"
        return AlignmentOutcome.ALIGNED, "PASS verdict with quiet IF window"

    if verdict == ExperimentVerdict.PARTIAL:
        if any_spike:
            return AlignmentOutcome.PARTIAL_MATCH, "PARTIAL verdict with IF spike (weak positive)"
        return AlignmentOutcome.MISSED_ANOMALY, "PARTIAL verdict without IF spike"

    return AlignmentOutcome.EXCLUDED, f"unknown verdict: {verdict}"


def evaluate_run_alignment(
    record: ExperimentRunRecord,
    dataset: TelemetryDataset,
    detector: AnomalyDetector,
    settings: ChaosGenSettings,
) -> RunAlignmentResult:
    features = features_from_dataset(dataset, settings)
    window_score = score_features_window(detector, features)

    outcome, note = classify_alignment(
        record.verdict,
        window_score.any_spike,
        aborted=record.aborted,
        dry_run=record.dry_run,
        feature_rows=window_score.feature_rows,
    )

    return RunAlignmentResult(
        run_id=record.run_id,
        experiment_name=record.experiment_name,
        verdict=record.verdict.value if record.verdict else None,
        ctk_status=record.ctk_status,
        aborted=record.aborted,
        dry_run=record.dry_run,
        window_start=record.window_start.isoformat(),
        window_end=record.window_end.isoformat(),
        journal_path=record.journal_path,
        telemetry_cache_path=record.telemetry_cache_path,
        telemetry_series=len(dataset.metrics),
        telemetry_samples=dataset.total_samples,
        window_score=window_score,
        alignment_outcome=outcome.value,
        alignment_note=note,
    )


def _build_confusion_matrix(runs: Sequence[RunAlignmentResult]) -> List[ConfusionCell]:
    counts: Dict[tuple[str, bool], int] = {}
    for run in runs:
        if run.alignment_outcome == AlignmentOutcome.EXCLUDED.value:
            continue
        verdict = run.verdict or "unknown"
        spike = run.window_score.any_spike
        key = (verdict, spike)
        counts[key] = counts.get(key, 0) + 1

    cells: List[ConfusionCell] = []
    for (verdict, spike), count in sorted(counts.items()):
        cells.append(ConfusionCell(verdict=verdict, if_spike=spike, count=count))
    return cells


def _build_summary(runs: Sequence[RunAlignmentResult]) -> AlignmentSummary:
    evaluable = [
        r for r in runs if r.alignment_outcome != AlignmentOutcome.EXCLUDED.value
    ]
    excluded = len(runs) - len(evaluable)
    aligned = sum(
        1
        for r in evaluable
        if r.alignment_outcome in {
            AlignmentOutcome.ALIGNED.value,
            AlignmentOutcome.PARTIAL_MATCH.value,
        }
    )

    fail_runs = [r for r in evaluable if r.verdict == ExperimentVerdict.FAIL.value]
    fail_spike = sum(1 for r in fail_runs if r.window_score.any_spike)
    pass_runs = [r for r in evaluable if r.verdict == ExperimentVerdict.PASS.value]
    pass_quiet = sum(1 for r in pass_runs if not r.window_score.any_spike)
    partial_runs = [r for r in evaluable if r.verdict == ExperimentVerdict.PARTIAL.value]
    partial_spike = sum(1 for r in partial_runs if r.window_score.any_spike)

    return AlignmentSummary(
        evaluable_runs=len(evaluable),
        excluded_runs=excluded,
        aligned_runs=aligned,
        alignment_rate=round(aligned / len(evaluable), 4) if evaluable else 0.0,
        fail_runs=len(fail_runs),
        fail_with_spike=fail_spike,
        fail_detection_rate=round(fail_spike / len(fail_runs), 4) if fail_runs else 0.0,
        pass_runs=len(pass_runs),
        pass_without_spike=pass_quiet,
        pass_specificity=round(pass_quiet / len(pass_runs), 4) if pass_runs else 0.0,
        partial_runs=len(partial_runs),
        partial_with_spike=partial_spike,
    )


def _thesis_narrative(summary: AlignmentSummary) -> str:
    return (
        "Isolation Forest was trained on healthy baseline telemetry (unsupervised). "
        "Per-run chaos windows were scored without leaking verdict labels into fit(). "
        f"On {summary.evaluable_runs} evaluable runs, alignment rate was "
        f"{summary.alignment_rate:.0%} "
        f"(FAIL detection {summary.fail_detection_rate:.0%}, "
        f"PASS specificity {summary.pass_specificity:.0%})."
    )


def load_cached_runs_from_dir(runs_dir: Union[str, Path]) -> List[tuple[ExperimentRunRecord, TelemetryDataset]]:
    root = Path(runs_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"runs directory not found: {root}")

    loaded: List[tuple[ExperimentRunRecord, TelemetryDataset]] = []
    for path in sorted(root.glob("*.json")):
        try:
            record, dataset = load_cached_run_telemetry(path)
            if not record.telemetry_cache_path:
                record = record.model_copy(update={"telemetry_cache_path": str(path.resolve())})
            loaded.append((record, dataset))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Skipping cache file %s: %s", path, exc)
    return loaded


def build_alignment_report(
    *,
    runs_dir: Union[str, Path] = DEFAULT_RUN_TELEMETRY_DIR,
    model_path: Optional[str] = None,
    config_path: Optional[str] = None,
    cached_runs: Optional[List[tuple[ExperimentRunRecord, TelemetryDataset]]] = None,
) -> AlignmentReport:
    settings = load_settings(config_path)
    detector, resolved_model, model_warnings = _build_detector(settings, model_path)

    if cached_runs is None:
        cached_runs = load_cached_runs_from_dir(runs_dir)

    results: List[RunAlignmentResult] = []
    for record, dataset in cached_runs:
        if model_warnings:
            record_warnings = list(model_warnings)
        else:
            record_warnings = []
        result = evaluate_run_alignment(record, dataset, detector, settings)
        if record_warnings:
            merged = list(result.window_score.warnings) + record_warnings
            result.window_score.warnings = merged
        results.append(result)

    summary = _build_summary(results)
    return AlignmentReport(
        model_path=str(resolved_model) if resolved_model else None,
        runs_dir=str(Path(runs_dir).resolve()),
        summary=summary,
        confusion_matrix=_build_confusion_matrix(results),
        runs=results,
        narrative=_thesis_narrative(summary),
    )


def render_alignment_markdown(report: AlignmentReport) -> str:
    lines: List[str] = [
        "# Anomaly ↔ Verdict Alignment Report",
        "",
        f"Generated: {report.generated_at}",
        f"Model: `{report.model_path or 'none (window refit)'}`",
        f"Runs directory: `{report.runs_dir}`",
        "",
        "## Thesis narrative",
        "",
        report.narrative,
        "",
        "## Summary metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Evaluable runs | {report.summary.evaluable_runs} |",
        f"| Excluded runs | {report.summary.excluded_runs} |",
        f"| Aligned runs | {report.summary.aligned_runs} |",
        f"| Alignment rate | {report.summary.alignment_rate:.2%} |",
        f"| FAIL runs | {report.summary.fail_runs} |",
        f"| FAIL with IF spike | {report.summary.fail_with_spike} |",
        f"| FAIL detection rate | {report.summary.fail_detection_rate:.2%} |",
        f"| PASS runs | {report.summary.pass_runs} |",
        f"| PASS without spike | {report.summary.pass_without_spike} |",
        f"| PASS specificity | {report.summary.pass_specificity:.2%} |",
        f"| PARTIAL runs | {report.summary.partial_runs} |",
        f"| PARTIAL with spike | {report.summary.partial_with_spike} |",
        "",
        "## Confusion-style matrix (Verdict × IF spike)",
        "",
        "| Verdict | IF spike | Count | Interpretation |",
        "| --- | --- | --- | --- |",
    ]

    for cell in report.confusion_matrix:
        interp = _cell_interpretation(cell.verdict, cell.if_spike)
        spike_label = "yes" if cell.if_spike else "no"
        lines.append(f"| {cell.verdict.upper()} | {spike_label} | {cell.count} | {interp} |")

    if not report.confusion_matrix:
        lines.append("| — | — | 0 | no evaluable runs |")

    lines.extend(
        [
            "",
            "## Per-run detail",
            "",
            "| Run | Verdict | IF spike | anomaly_fraction | max_severity | Outcome |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )

    for run in report.runs:
        spike = "yes" if run.window_score.any_spike else "no"
        sev = run.window_score.max_severity or "—"
        lines.append(
            f"| {run.experiment_name} | "
            f"{(run.verdict or '—').upper()} | {spike} | "
            f"{run.window_score.anomaly_fraction:.2%} | {sev} | "
            f"{run.alignment_outcome} |"
        )

    lines.extend(["", "## Notes", "", "- IF remains unsupervised; verdict labels are validation only.", "- PARTIAL + spike counts as weak positive alignment.", "- Aborted/dry-run/empty telemetry runs are excluded.", ""])
    return "\n".join(lines)


def _cell_interpretation(verdict: str, if_spike: bool) -> str:
    v = verdict.lower()
    if v == "fail" and if_spike:
        return "correct detection"
    if v == "fail" and not if_spike:
        return "missed chaos signal"
    if v == "pass" and not if_spike:
        return "correct quiet window"
    if v == "pass" and if_spike:
        return "false alarm"
    if v == "partial" and if_spike:
        return "weak positive"
    if v == "partial" and not if_spike:
        return "residual risk, no spike"
    return "—"


def write_alignment_artifacts(
    report: AlignmentReport,
    *,
    markdown_path: Union[str, Path],
    json_path: Optional[Union[str, Path]] = None,
) -> tuple[Path, Optional[Path]]:
    md_path = Path(markdown_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_alignment_markdown(report), encoding="utf-8")

    json_out: Optional[Path] = None
    if json_path:
        json_out = Path(json_path)
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    return md_path, json_out


def enrich_records_from_catalog(
    records: List[ExperimentRunRecord],
    *,
    verdict_path: Optional[Union[str, Path]] = None,
    journal_dir: Optional[Union[str, Path]] = None,
) -> List[ExperimentRunRecord]:
    """Merge verdict labels onto cached records keyed by journal_path."""
    catalog = build_run_records(
        journal_dir=journal_dir,
        verdict_path=verdict_path,
    )
    by_journal = {r.journal_path: r for r in catalog if r.journal_path}
    enriched: List[ExperimentRunRecord] = []
    for rec in records:
        if rec.journal_path and rec.journal_path in by_journal:
            src = by_journal[rec.journal_path]
            enriched.append(
                rec.model_copy(
                    update={
                        "verdict": src.verdict,
                        "aborted": src.aborted,
                        "dry_run": src.dry_run,
                        "ctk_status": src.ctk_status or rec.ctk_status,
                        "deviated": src.deviated,
                    }
                )
            )
        else:
            enriched.append(rec)
    return enriched
