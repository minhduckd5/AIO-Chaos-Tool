#!/usr/bin/env python3
"""
Train one model per public dataset folder (smoke test before merged auto_train).

Writes models/per/<slug>.joblib and models/per/manifest.json with pass/fail rows.

Usage:
    python scripts/train_each_dataset.py
    python scripts/train_each_dataset.py -v
    python scripts/train_each_dataset.py --only eadro,nezha
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = PROJECT_ROOT / "data" / "public-datasets"
PER_DIR = PROJECT_ROOT / "models" / "per"
AUTO_TRAIN = PROJECT_ROOT / "scripts" / "auto_train.py"

# slug -> (folder under public-datasets/, optional subfolder to avoid 2M-file scans)
TRAIN_DATASETS: dict[str, tuple[str, str | None]] = {
    "aiops-challenge-2020": ("AIOps-Challenge-2020-Data", None),
    "cloud-opsbench": ("Cloud-OpsBench-main", "benchmark"),
    "eadro": (
        "Traces, Metrics, and Logs for Anomaly Detection and Root Cause Localization in Microservices",
        "TT Dataset/data/TT.2022-04-19T001753D2022-04-19T020534",
    ),
    "gaia": ("GAIA-DataSet-main", "Companion_Data/metric_detection.zip"),
    "lo2v2": ("LO2v2", "LO2v2-metrics"),
    "loghub": ("Loghub", "preprocessed"),
    "multidimension": (
        "MultiDimension-Localization",
        "MultiDimension-Localization-master/part1",
    ),
    "nezha": ("Nezha-main", "rca_data"),
    "open5gs": ("Open5GS", None),
    "petshop": ("petshop-root-cause-analysis-main", None),
    "rcaeval": ("RCAEval-v2~", "data/re3tt_ts-route-service_f2_1"),
    "traintickettrace": (
        "TrainTicketTrace",
        "2024-05-06-17-01-11-ts-error-F1-generated-with-ts-admin-basic-info-service/monitoring-prometheus",
    ),
}


def _resolve_export_path(folder: str, subfolder: str | None) -> Path:
    root = DATASETS_ROOT / folder
    if subfolder:
        candidate = root / subfolder
        if candidate.exists():
            return candidate
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-dataset training smoke tests.")
    parser.add_argument(
        "--only", type=str, default=None,
        help="Comma-separated slugs to run (default: all TRAIN datasets on disk)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    slugs = list(TRAIN_DATASETS.keys())
    if args.only:
        slugs = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = set(slugs) - set(TRAIN_DATASETS)
        if unknown:
            print(f"Unknown slug(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            sys.exit(2)

    PER_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "results": [],
    }

    print(f"\nPer-dataset training -> {PER_DIR}\n")

    for slug in slugs:
        folder_name, subfolder = TRAIN_DATASETS[slug]
        export_path = _resolve_export_path(folder_name, subfolder)
        output = PER_DIR / f"{slug}.joblib"

        if not export_path.exists():
            row = {
                "slug": slug,
                "folder": folder_name,
                "subfolder": subfolder,
                "status": "missing",
                "model": None,
                "elapsed_s": 0,
            }
            manifest["results"].append(row)
            print(f"[MISSING] {slug} ({export_path})")
            continue

        cmd = [
            sys.executable, "-u", str(AUTO_TRAIN),
            "--export-path", str(export_path),
            "--output", str(output),
            "--no-lock",
        ]
        if args.verbose:
            cmd.append("-v")

        print(f"\n{'='*60}\n  START {slug}\n  {export_path}\n{'='*60}")
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        elapsed = time.perf_counter() - t0

        ok = proc.returncode == 0 and output.is_file()
        status = "ok" if ok else "failed"
        row = {
            "slug": slug,
            "folder": folder_name,
            "subfolder": subfolder,
            "export_path": str(export_path),
            "status": status,
            "model": str(output) if ok else None,
            "exit_code": proc.returncode,
            "elapsed_s": round(elapsed, 1),
        }
        manifest["results"].append(row)
        print(f"\n  [{status.upper()}] {slug} in {elapsed:.1f}s\n")

    manifest["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    manifest_path = PER_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    ok_n = sum(1 for r in manifest["results"] if r["status"] == "ok")
    fail_n = sum(1 for r in manifest["results"] if r["status"] == "failed")
    miss_n = sum(1 for r in manifest["results"] if r["status"] == "missing")

    print(f"\n{'='*60}")
    print(f"  MANIFEST: {manifest_path}")
    print(f"  OK: {ok_n} | FAILED: {fail_n} | MISSING: {miss_n}")
    print(f"{'='*60}\n")

    for row in manifest["results"]:
        print(f"  {row['status']:7}  {row['slug']}")

    if fail_n or miss_n:
        sys.exit(1)


if __name__ == "__main__":
    main()
