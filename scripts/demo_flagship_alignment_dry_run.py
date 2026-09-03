#!/usr/bin/env python3
"""
Flagship demo dry-run: before/after retrain alignment metrics for thesis slides.

Uses CTK journal fixtures + synthetic telemetry caches (no live K3s required).
For defense rehearsal; replace caches with live fetch-run-telemetry output when lab is up.

Usage:
  python scripts/demo_flagship_alignment_dry_run.py
  python scripts/demo_flagship_alignment_dry_run.py --output-dir docs/demo-alignment
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chaosgen.evaluation.anomaly_corpus import (
    ContaminationMode,
    CtkVerdictFilter,
    build_retrain_corpus,
    load_baseline_features,
)
from chaosgen.evaluation.run_catalog import record_from_journal
from chaosgen.evaluation.run_telemetry import cache_run_telemetry
from chaosgen.evaluation.verdict_alignment import (
    build_alignment_report,
    write_alignment_artifacts,
)
from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.canonical_features import CANONICAL_SCHEMA_VERSION
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.schemas.scenarios import ExperimentVerdict
from chaosgen.schemas.telemetry import MetricSample, TelemetryDataset, TimeSeries

FIXTURES = ROOT / "tests" / "fixtures" / "ctk_journal"
DEFAULT_OUT = ROOT / "docs" / "demo-alignment"


class _StubCollector:
    def __init__(self, datasets: dict[str, TelemetryDataset]):
        self._datasets = datasets

    def collect_range(self, start, end, step="60s"):
        key = f"{start.isoformat()}_{end.isoformat()}"
        return self._datasets.get(key) or TelemetryDataset(
            metrics=[],
            logs=[],
            collection_start=start,
            collection_end=end,
        )


def _series(
    start_ts: float,
    n: int,
    base: float,
    spike: bool,
    service: str = "frontend",
) -> TimeSeries:
    samples = []
    for i in range(n):
        val = base
        if spike and 8 <= i < 14:
            val = base * 4.0 + i
        samples.append(
            MetricSample(
                timestamp=start_ts + i * 60,
                value=val,
                labels={"service": service},
            )
        )
    return TimeSeries(metric_name="request_rate", labels={"service": service}, samples=samples)


def _dataset_for_record(record, spike: bool) -> TelemetryDataset:
    start_ts = record.window_start.timestamp()
    n = max(12, int((record.window_end - record.window_start).total_seconds() // 60))
    metrics = [
        _series(start_ts, n, 100.0, spike, "frontend"),
        _series(start_ts, n, 80.0, spike, "checkout"),
    ]
    return TelemetryDataset(
        metrics=metrics,
        logs=[],
        collection_start=record.window_start,
        collection_end=record.window_end,
    )


def _write_baseline_export(path: Path, rows: int = 200) -> Path:
    """Minimal wide CSV baseline for train / retrain corpus."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["timestamp,request_rate__frontend__mean,error_rate__checkout__mean"]
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(rows):
        ts = base.timestamp() + i * 60
        lines.append(f"{ts},100.0,0.01")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _train_baseline_model(export_path: Path, out_path: Path, settings) -> None:
    from sklearn.ensemble import IsolationForest

    features = load_baseline_features([export_path], settings)
    detector = AnomalyDetector(settings=settings.anomaly)
    if settings.features.canonical_enabled:
        detector.canonical_schema_version = CANONICAL_SCHEMA_VERSION
    detector.fit(features)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    detector.save_model(str(out_path))


def _retrain_model(
    export_path: Path,
    runs_dir: Path,
    out_path: Path,
    settings,
) -> None:
    from sklearn.ensemble import IsolationForest

    corpus = build_retrain_corpus(
        [export_path],
        settings,
        include_ctk_runs_dir=runs_dir,
        verdict_filter=CtkVerdictFilter.PASS_ONLY,
        contamination_mode=ContaminationMode.PROPORTIONAL,
        max_ctk_fraction=0.10,
    )
    detector = AnomalyDetector(settings=settings.anomaly)
    detector.contamination = corpus.contamination
    detector.iso_forest = IsolationForest(
        contamination=corpus.contamination,
        random_state=detector.random_state,
        n_jobs=-1,
    )
    if settings.features.canonical_enabled:
        detector.canonical_schema_version = CANONICAL_SCHEMA_VERSION
    detector.fit(corpus.features)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    detector.save_model(str(out_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Flagship alignment dry-run for slides")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--config", default=str(ROOT / "examples" / "registry-vm-settings.yaml"))
    args = parser.parse_args()

    from chaosgen.config.settings import load_settings

    out = args.output_dir
    scratch_runs = ROOT / "scratch" / "telemetry" / "runs"
    scratch_runs.mkdir(parents=True, exist_ok=True)

    for old in scratch_runs.glob("ctk-*.json"):
        old.unlink()

    settings = load_settings(args.config)
    export_path = _write_baseline_export(ROOT / "scratch" / "demo" / "baseline.csv")

    journal_plan = [
        (FIXTURES / "completed_no_deviation.json", ExperimentVerdict.PASS, False),
        (FIXTURES / "completed_deviated.json", ExperimentVerdict.FAIL, True),
        (FIXTURES / "activity_failure.json", ExperimentVerdict.FAIL, True),
    ]

    stub_datasets: dict[str, TelemetryDataset] = {}
    cached_pairs = []

    for journal_path, verdict, spike in journal_plan:
        record = record_from_journal(journal_path, padding_seconds=0)
        record = record.model_copy(update={"verdict": verdict})
        dataset = _dataset_for_record(record, spike=spike)
        key = f"{record.window_start.isoformat()}_{record.window_end.isoformat()}"
        stub_datasets[key] = dataset
        cached = cache_run_telemetry(
            record,
            _StubCollector(stub_datasets),
            output_dir=scratch_runs,
        )
        cached_pairs.append((cached, dataset))

    models_dir = ROOT / "models"
    baseline_model = models_dir / "demo_baseline.joblib"
    retrained_model = models_dir / "demo_retrained.joblib"

    _train_baseline_model(export_path, baseline_model, settings)
    report_before = build_alignment_report(
        runs_dir=scratch_runs,
        model_path=str(baseline_model),
        config_path=args.config,
        cached_runs=cached_pairs,
    )
    write_alignment_artifacts(
        report_before,
        markdown_path=out / "anomaly-verdict-alignment-before.md",
        json_path=out / "anomaly-verdict-alignment-before.json",
    )

    _retrain_model(export_path, scratch_runs, retrained_model, settings)
    report_after = build_alignment_report(
        runs_dir=scratch_runs,
        model_path=str(retrained_model),
        config_path=args.config,
        cached_runs=cached_pairs,
    )
    write_alignment_artifacts(
        report_after,
        markdown_path=out / "anomaly-verdict-alignment-after.md",
        json_path=out / "anomaly-verdict-alignment-after.json",
    )

    comparison = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "flagship": "multi-target pod kill (fixture journals)",
        "before": {
            "fail_detection_rate": report_before.summary.fail_detection_rate,
            "pass_specificity": report_before.summary.pass_specificity,
            "alignment_rate": report_before.summary.alignment_rate,
            "model": str(baseline_model),
        },
        "after": {
            "fail_detection_rate": report_after.summary.fail_detection_rate,
            "pass_specificity": report_after.summary.pass_specificity,
            "alignment_rate": report_after.summary.alignment_rate,
            "model": str(retrained_model),
        },
        "delta": {
            "fail_detection_rate": round(
                report_after.summary.fail_detection_rate
                - report_before.summary.fail_detection_rate,
                4,
            ),
            "pass_specificity": round(
                report_after.summary.pass_specificity
                - report_before.summary.pass_specificity,
                4,
            ),
        },
        "artifacts": {
            "before_md": str((out / "anomaly-verdict-alignment-before.md").resolve()),
            "after_md": str((out / "anomaly-verdict-alignment-after.md").resolve()),
            "before_json": str((out / "anomaly-verdict-alignment-before.json").resolve()),
            "after_json": str((out / "anomaly-verdict-alignment-after.json").resolve()),
        },
    }
    summary_path = out / "flagship-alignment-comparison.json"
    summary_path.write_text(json.dumps(comparison, indent=2), encoding="utf-8")

    print("=== Flagship alignment dry-run ===")
    print(
        f"FAIL detection: {comparison['before']['fail_detection_rate']:.0%} -> "
        f"{comparison['after']['fail_detection_rate']:.0%} "
        f"(delta {comparison['delta']['fail_detection_rate']:+.0%})"
    )
    print(f"PASS specificity: {comparison['before']['pass_specificity']:.0%} -> {comparison['after']['pass_specificity']:.0%}")
    print(f"Comparison JSON: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
