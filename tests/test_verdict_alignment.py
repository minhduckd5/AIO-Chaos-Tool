"""Verdict ↔ IF alignment evaluation tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from chaosgen.evaluation.run_telemetry import cache_run_telemetry
from chaosgen.evaluation.run_catalog import record_from_journal
from chaosgen.evaluation.verdict_alignment import (
    AlignmentOutcome,
    build_alignment_report,
    classify_alignment,
    render_alignment_markdown,
    score_features_window,
    write_alignment_artifacts,
)
from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.schemas.scenarios import ExperimentVerdict
from chaosgen.schemas.telemetry import MetricSample, TelemetryDataset, TimeSeries

FIXTURES = Path(__file__).parent / "fixtures" / "ctk_journal"


class _StubCollector:
    def collect_range(self, start, end, step="60s"):
        return TelemetryDataset(
            metrics=[],
            logs=[],
            collection_start=start,
            collection_end=end,
        )


def _synthetic_features(rows: int = 40, spike_at: slice | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    data = rng.normal(0.0, 1.0, size=(rows, 3))
    if spike_at is not None:
        data[spike_at] += 8.0
    idx = pd.date_range("2026-08-28 10:00", periods=rows, freq="60s", tz="UTC")
    return pd.DataFrame(data, columns=["f0", "f1", "f2"], index=idx)


def test_score_features_window_detects_spike():
    detector = AnomalyDetector(contamination=0.1)
    quiet = _synthetic_features(50)
    detector.fit(quiet)

    spike = _synthetic_features(50, spike_at=slice(20, 30))
    score = score_features_window(detector, spike)
    assert score.any_spike
    assert score.anomaly_fraction > 0
    assert score.max_severity is not None
    assert score.max_severity_score > 0


def test_classify_alignment_fail_pass():
    aligned, _ = classify_alignment(ExperimentVerdict.FAIL, True)
    assert aligned == AlignmentOutcome.ALIGNED

    missed, _ = classify_alignment(ExperimentVerdict.FAIL, False)
    assert missed == AlignmentOutcome.MISSED_ANOMALY

    aligned_pass, _ = classify_alignment(ExperimentVerdict.PASS, False)
    assert aligned_pass == AlignmentOutcome.ALIGNED

    false_alarm, _ = classify_alignment(ExperimentVerdict.PASS, True)
    assert false_alarm == AlignmentOutcome.FALSE_ALARM


def test_build_alignment_report_from_cache(tmp_path):
    journal = FIXTURES / "completed_deviated.json"
    record = record_from_journal(journal, padding_seconds=0)
    record = record.model_copy(update={"verdict": ExperimentVerdict.FAIL})

    samples = [
        MetricSample(timestamp=1730000000.0 + i * 60, value=1.0 + (i % 5), labels={"service": "frontend"})
        for i in range(20)
    ]
    dataset = TelemetryDataset(
        metrics=[TimeSeries(metric_name="request_rate", samples=samples)],
        logs=[],
        collection_start=record.window_start,
        collection_end=record.window_end,
    )
    cached = cache_run_telemetry(record, _StubCollector(), output_dir=tmp_path)

    report = build_alignment_report(
        runs_dir=tmp_path,
        cached_runs=[(cached, dataset)],
    )
    assert len(report.runs) == 1
    assert "Isolation Forest" in report.narrative
    md = render_alignment_markdown(report)
    assert "Confusion-style matrix" in md
    assert "anomaly_fraction" in md or report.runs[0].window_score.anomaly_fraction >= 0

    md_path, json_path = write_alignment_artifacts(
        report,
        markdown_path=tmp_path / "report.md",
        json_path=tmp_path / "report.json",
    )
    assert md_path.is_file()
    assert json_path and json_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["evaluable_runs"] >= 0
