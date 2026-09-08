"""Lab rehearsal: Step 1 Dual-Mode ingest against registry-vm."""

from __future__ import annotations

import hashlib
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROM = os.environ.get("CHAOSGEN_PROM_URL", "http://127.0.0.1:9090")
LOKI = os.environ.get("CHAOSGEN_LOKI_URL", "http://127.0.0.1:3100")
NS = os.environ.get("CHAOSGEN_NAMESPACE", "default")
SETTINGS = Path.home() / ".config" / "chaosgen" / "settings.yaml"


def fingerprint(path: Path) -> tuple[str | None, float | None, int | None]:
    if not path.exists():
        return None, None, None
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()[:16], path.stat().st_mtime, len(data)


def step_ping_prefetch() -> dict:
    from chaosgen.gui.analysis_pipeline import (
        check_telemetry_endpoints,
        probe_guided_catalog_endpoints,
    )

    print("=== B1 Ping ===")
    t0 = time.perf_counter()
    health = check_telemetry_endpoints(PROM, LOKI)
    ping_ms = (time.perf_counter() - t0) * 1000
    for k, v in health.items():
        print(f"  {k}: {v}")
    all_ok = all(not str(m).startswith("FAIL") for m in health.values())
    print(f"  all_ok={all_ok} ping_ms={ping_ms:.0f}")

    print("=== B1 Prefetch ===")
    t1 = time.perf_counter()
    out = probe_guided_catalog_endpoints(PROM, LOKI, NS)
    prefetch_ms = (time.perf_counter() - t1) * 1000
    print(f"  ok={out.get('ok')} prefetch_ms={prefetch_ms:.0f}")
    catalog = out.get("catalog")
    suggested = []
    buckets: dict[str, int] = {}
    if catalog is not None:
        suggested = [q for q in catalog.queries if q.suggested]
        for q in catalog.queries:
            buckets[q.bucket] = buckets.get(q.bucket, 0) + 1
        print(
            f"  queries={len(catalog.queries)} suggested={len(suggested)} "
            f"stack={catalog.stack_summary!r}"
        )
        print(f"  warnings={catalog.warnings}")
        print(f"  buckets={buckets}")
        print("  --- Suggested ---")
        for q in suggested:
            print(f"   * [{q.bucket}] {q.metric}")
            print(f"     {q.query[:100]}")
    elif not out.get("ok"):
        print(f"  error={out.get('error')}")

    return {
        "health": health,
        "all_ok": all_ok,
        "ping_ms": ping_ms,
        "prefetch_ms": prefetch_ms,
        "prefetch_ok": bool(out.get("ok")),
        "catalog": catalog,
        "suggested": suggested,
        "buckets": buckets,
    }


def step_ui_checklist(catalog) -> dict:
    """Offscreen Qt: Suggested / Deselect / zero-guard / re-Suggested."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt

    from chaosgen.gui.controller import AppController
    from chaosgen.gui.views.advisor_view import AdvisorView

    app = QApplication.instance() or QApplication([])
    ctrl = AppController()
    view = AdvisorView(ctrl)

    # Simulate connected + catalog ready
    view._telemetry_connected = True
    view._ingest_mode_combo.setEnabled(True)
    view._guided_catalog = catalog
    view._guided_prefetch_pending = False
    view._guided_prefetch_error = None

    # Switch to Custom
    idx = view._ingest_mode_combo.findData("custom")
    view._ingest_mode_combo.setCurrentIndex(idx)
    QApplication.processEvents()

    panel_visible = not view._custom_panel.isHidden()
    beta_visible = not view._ingest_beta_badge.isHidden()
    groups = view._guided_tree.topLevelItemCount()
    status = view._custom_status_label.text()

    # Suggested
    view._on_guided_suggested()
    QApplication.processEvents()
    n_sug = len(view._selected_guided_queries())
    run_after_sug = view._analyze_btn.isEnabled()
    hint_after_sug = not view._custom_zero_hint.isHidden()

    # Deselect all
    view._set_all_guided_checks(False)
    QApplication.processEvents()
    n_zero = len(view._selected_guided_queries())
    run_after_zero = view._analyze_btn.isEnabled()
    hint_after_zero = not view._custom_zero_hint.isHidden()
    hint_text = view._custom_zero_hint.text()

    # Suggested again
    view._on_guided_suggested()
    QApplication.processEvents()
    run_again = view._analyze_btn.isEnabled()

    # Mode labels
    default_label = view._ingest_mode_combo.itemText(0)
    custom_label = view._ingest_mode_combo.itemText(1)
    strip = view._ingest_status_label.text()

    print("=== B2/B3 Offscreen UI ===")
    print(f"  mode_labels: {default_label!r} | {custom_label!r}")
    print(f"  custom_panel_visible={panel_visible} beta_badge={beta_visible}")
    print(f"  tree_groups={groups} status={status!r}")
    print(f"  strip={strip!r}")
    print(f"  after Suggested: selected={n_sug} run_enabled={run_after_sug} hint={hint_after_sug}")
    print(f"  after Deselect: selected={n_zero} run_enabled={run_after_zero} hint={hint_after_zero}")
    print(f"  hint_text={hint_text!r}")
    print(f"  after re-Suggested: run_enabled={run_again}")

    # In-flight UI path
    view._guided_catalog = None
    view._guided_prefetch_pending = True
    view._render_guided_panel()
    inflight = view._custom_status_label.text()
    print(f"  inflight_status={inflight!r}")

    return {
        "panel_visible": panel_visible,
        "beta_visible": beta_visible,
        "groups": groups,
        "n_suggested_selected": n_sug,
        "run_after_suggested": run_after_sug,
        "run_after_deselect": run_after_zero,
        "hint_after_deselect": hint_after_zero,
        "hint_text": hint_text,
        "inflight_status": inflight,
        "run_after_re_suggested": run_again,
        "selected_for_run": [asdict(q) for q in view._selected_guided_queries()],
    }


def step_custom_run(selected_dicts: list[dict]) -> dict:
    from chaosgen.gui.analysis_pipeline import AnalysisRequest, run_gui_analysis_pipeline

    before = fingerprint(SETTINGS)
    print("=== B4 Custom Run (1h) ===")
    print(f"  settings_before hash={before[0]} mtime={before[1]} size={before[2]}")
    print(f"  custom_queries={len(selected_dicts)}")

    req = AnalysisRequest(
        source="live",
        prom_url=PROM,
        loki_url=LOKI,
        lookback_hours=1,
        generate_scenarios=False,
        ingest_mode="custom",
        custom_queries=selected_dicts,
        scope_namespace=NS,
        config_path=str(ROOT / "examples" / "registry-vm-settings.yaml"),
    )
    t0 = time.perf_counter()
    result = run_gui_analysis_pipeline(req)
    dt = time.perf_counter() - t0
    after = fingerprint(SETTINGS)

    print(f"  elapsed_s={dt:.1f}")
    print(f"  series={result.metric_series} samples={result.total_samples} features={result.feature_rows}")
    print(f"  clusters={len(result.clusters)} source={result.source_label}")
    print(f"  settings_after hash={after[0]} mtime={after[1]} size={after[2]}")
    print(f"  settings_unchanged={before == after}")

    return {
        "elapsed_s": dt,
        "metric_series": result.metric_series,
        "total_samples": result.total_samples,
        "feature_rows": result.feature_rows,
        "clusters": len(result.clusters),
        "settings_before": before,
        "settings_after": after,
        "settings_unchanged": before == after,
        "source_label": result.source_label,
    }


def main() -> int:
    print(f"lab start {datetime.now(timezone.utc).isoformat()}")
    ping = step_ping_prefetch()
    if not ping["all_ok"] or ping["catalog"] is None:
        print("ABORT: ping/prefetch failed")
        return 1
    ui = step_ui_checklist(ping["catalog"])
    # Prefer Suggested selection from UI; fallback to catalog.suggested
    selected = ui.get("selected_for_run") or [asdict(q) for q in ping["suggested"]]
    if not selected:
        print("ABORT: no suggested queries")
        return 1
    run = step_custom_run(selected)

    print("\n=== VERDICT ===")
    checks = {
        "B1_ping_ok": ping["all_ok"],
        "B1_prefetch_ok": ping["prefetch_ok"],
        "B1_suggested_4_to_6": 4 <= len(ping["suggested"]) <= 6,
        "B2_custom_panel": ui["panel_visible"],
        "B2_groups_gt0": ui["groups"] > 0,
        "B2_inflight_copy": "Discovering live signals" in ui["inflight_status"],
        "B3_deselect_disables_run": ui["run_after_deselect"] is False,
        "B3_hint_visible": ui["hint_after_deselect"] is True,
        "B3_hint_text": "Select at least one metric" in ui["hint_text"],
        "B3_suggested_reenables": ui["run_after_re_suggested"] is True,
        "B4_got_series": run["metric_series"] > 0,
        "B4_settings_isolation": run["settings_unchanged"],
    }
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}={v}")
    failed = [k for k, v in checks.items() if not v]
    print(f"failed={failed or 'none'}")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
