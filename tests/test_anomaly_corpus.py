"""Anomaly corpus merge and contamination policy tests."""

from __future__ import annotations

import pandas as pd

from chaosgen.evaluation.anomaly_corpus import (
    ContaminationMode,
    compute_fit_contamination,
    _align_and_merge,
)


def test_compute_contamination_baseline_only():
    c, capped = compute_fit_contamination(
        1000, 500, 0.1, mode=ContaminationMode.BASELINE_ONLY, max_ctk_fraction=0.5
    )
    assert capped == 0
    assert c == 0.1


def test_compute_contamination_proportional_reduces_with_ctk():
    c, capped = compute_fit_contamination(
        1000, 500, 0.1, mode=ContaminationMode.PROPORTIONAL, max_ctk_fraction=0.5
    )
    assert capped == 500
    assert c < 0.1
    assert c >= 0.01


def test_compute_contamination_caps_ctk_fraction():
    c, capped = compute_fit_contamination(
        1000, 500, 0.1, mode=ContaminationMode.PROPORTIONAL, max_ctk_fraction=0.10
    )
    assert capped == 100
    assert c < 0.1


def test_align_and_merge_caps_ctk_rows():
    baseline = pd.DataFrame({"a": range(100)}, index=pd.RangeIndex(100))
    ctk = pd.DataFrame({"a": range(200, 250)}, index=pd.RangeIndex(200, 250))
    merged = _align_and_merge(baseline, ctk, capped_ctk_rows=10)
    assert len(merged) == 110
