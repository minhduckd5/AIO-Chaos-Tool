"""WS-6 — demo_multi_arch_matrix smoke test."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.demo_multi_arch_matrix import MATRIX_ARCHITECTURES, _evaluate_profile


def test_matrix_covers_six_profiles_with_valid_connect():
    rows = [_evaluate_profile(arch, dry_run=True, live_inject=False) for arch in MATRIX_ARCHITECTURES]
    assert len(rows) == 6
    assert all(r.connect_valid for r in rows)
    assert {r.architecture for r in rows} == {a.value for a in MATRIX_ARCHITECTURES}


def test_matrix_json_roundtrip(tmp_path):
    rows = [_evaluate_profile(arch, dry_run=True, live_inject=False) for arch in MATRIX_ARCHITECTURES]
    out = tmp_path / "matrix.json"
    from dataclasses import asdict

    out.write_text(json.dumps({"profiles": [asdict(r) for r in rows]}), encoding="utf-8")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["profiles"]) == 6
