#!/usr/bin/env python3
"""
Auto-train: walk public-datasets/, discover trainable exports, produce one merged model.

Usage:
    python scripts/auto_train.py
    python scripts/auto_train.py --datasets-root H:/data/public-datasets
    python scripts/auto_train.py --output models/merged.joblib --max-exports 5
    python scripts/auto_train.py --include-ext .csv,.json,.log

Runs in-process with a progress bar, ETA, and per-export elapsed time.
"""
from __future__ import annotations

import argparse
import ctypes
import gc
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DATASETS_ROOT = PROJECT_ROOT / "data" / "public-datasets"
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "merged_public.joblib"
LOCK_FILE = PROJECT_ROOT / "models" / ".auto_train.lock"

# Hard defaults tuned for 64 GB RAM — stay well below OOM.
DEFAULT_MEM_RESERVE_FRAC = 0.15      # keep ~15% physical RAM free
DEFAULT_MEM_MAX_FRAC = 0.35          # budget for kept feature matrix (~22 GB on 64 GB)
DEFAULT_MAX_CSV_FILES = 32           # per export folder
DEFAULT_MAX_CSV_MB = 256             # per export folder
DEFAULT_MAX_FEATURE_ROWS = 150_000   # global cap after merge
DEFAULT_MAX_EXPORTS = 5
DEFAULT_MAX_METRIC_SERIES = 150      # per export before feature engineering
DEFAULT_MAX_SAMPLES_PER_SERIES = 2000

from chaosgen.ingestion.trainable_filter import (
    DEFAULT_TRAINABLE_EXTENSIONS,
    SKIP_DIRS,
    is_noise_path,
    matches_extension,
)

DEFAULT_INCLUDE_EXT = set(DEFAULT_TRAINABLE_EXTENSIONS)

ARCHIVE_EXTENSIONS = {".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2"}
SKIP_ARCHIVE_DISCOVERY = True

MAX_CSV_SIZE_MB = 2000

# Discovery strategy for gigantic extracted datasets:
# - We only need to decide which top-level dataset folders to pass into ExportLoader.
# - For speed, we scan for the first matching trainable file and then stop.
DISCOVERY_SCAN_MAX_DEPTH = 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_extensions(raw: str) -> set[str]:
    """Parse comma-separated extension list like '.csv,.json,.log'."""
    exts = set()
    for tok in raw.split(","):
        tok = tok.strip().lower()
        if tok and not tok.startswith("."):
            tok = "." + tok
        if tok:
            exts.add(tok)
    return exts


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _fmt_bytes(nbytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(nbytes)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.2f}{u}"
        v /= 1024.0
    return f"{nbytes}B"


def _get_phys_mem_total_avail_bytes() -> tuple[int, int]:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    ms = MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    return int(ms.ullTotalPhys), int(ms.ullAvailPhys)


def _log(msg: str) -> None:
    """Print with flush; replace unencodable chars on Windows cp1252 consoles."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode(enc, errors="replace").decode(enc), flush=True)


def _acquire_single_instance_lock():
    """Refuse to start if another auto_train.py is already running."""
    import msvcrt

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        logger.error(
            "Another auto_train run is active (lock: %s). "
            "Stop the other Python process first.",
            LOCK_FILE,
        )
        sys.exit(2)
    handle.write(str(time.time()))
    handle.flush()
    return handle


def _merge_features(
    combined,
    features,
    *,
    max_feature_rows: int,
):
    """Incrementally merge feature frames; trim to row cap."""
    import pandas as pd

    if combined is None:
        combined = features
    else:
        combined = pd.concat([combined, features], axis=0, sort=True)

    combined = combined.fillna(0.0)
    combined = combined[~combined.index.duplicated(keep="first")].sort_index()

    if len(combined) > max_feature_rows:
        combined = combined.iloc[-max_feature_rows:].copy()
        _log(f"       [!] Trimmed merged features to last {max_feature_rows:,} rows")

    return combined


def _trim_dataset(dataset, *, max_metric_series: int, max_samples_per_series: int):
    """Downsample huge public dumps before feature engineering."""
    from chaosgen.schemas.telemetry import MetricSample, TelemetryDataset, TimeSeries

    metrics = [ts for ts in dataset.metrics if ts.samples]
    if len(metrics) > max_metric_series:
        metrics = sorted(metrics, key=lambda ts: len(ts.samples), reverse=True)[:max_metric_series]
        _log(
            f"       [!] Trimmed metric series to top {max_metric_series} "
            f"by sample count"
        )

    trimmed: list[TimeSeries] = []
    for ts in metrics:
        samples: list[MetricSample] = sorted(ts.samples, key=lambda s: s.timestamp)
        if len(samples) > max_samples_per_series:
            step = max(1, len(samples) // max_samples_per_series)
            samples = samples[::step][:max_samples_per_series]
        trimmed.append(
            TimeSeries(metric_name=ts.metric_name, labels=ts.labels, samples=samples)
        )

    return TelemetryDataset(
        metrics=trimmed,
        logs=dataset.logs,
        collection_start=dataset.collection_start,
        collection_end=dataset.collection_end,
        source_cluster=dataset.source_cluster,
    )


def is_trainable_file(path: Path, include_ext: set[str]) -> bool:
    if is_noise_path(path):
        return False
    if not matches_extension(path, include_ext):
        return False
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_CSV_SIZE_MB:
        logger.warning("Skipping oversized file (%.0f MB): %s", size_mb, path)
        return False
    return True


def has_prometheus_bundle(d: Path) -> bool:
    return (d / "prometheus").is_dir()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _iter_shallow(root: Path, max_depth: int = 2, _depth: int = 0):
    """Yield files up to max_depth levels below root (avoids full rglob on huge trees)."""
    try:
        for child in root.iterdir():
            if child.is_file():
                yield child
            elif child.is_dir() and _depth < max_depth and child.name.lower() not in SKIP_DIRS:
                yield from _iter_shallow(child, max_depth, _depth + 1)
    except PermissionError:
        pass


def discover_exports(root: Path, max_exports: int, include_ext: set[str] | None = None) -> list[Path]:
    if include_ext is None:
        include_ext = DEFAULT_INCLUDE_EXT

    exports: list[Path] = []

    if not root.is_dir():
        logger.error("Datasets root not found: %s", root)
        return exports

    for entry in sorted(root.iterdir()):
        if len(exports) >= max_exports:
            break
        if not entry.is_dir():
            # Only dataset folders — ignore stray MANIFEST.txt etc. at root
            continue

        if entry.name.lower() in SKIP_DIRS:
            continue

        if has_prometheus_bundle(entry):
            exports.append(entry)
            continue

        # Fast existence check: find the first trainable file within the scan budget.
        found_trainable = False
        for child in _iter_shallow(entry, max_depth=DISCOVERY_SCAN_MAX_DEPTH):
            if child.is_file() and is_trainable_file(child, include_ext):
                found_trainable = True
                break

        if found_trainable:
            exports.append(entry)

    return exports


def _pick_best_subpath(
    root: Path,
    csvs: list[Path],
    jsons: list[Path],
    archives: list[Path],
) -> Path | None:
    for subdir in sorted(root.iterdir()):
        if subdir.is_dir() and has_prometheus_bundle(subdir):
            return subdir

    large_csvs = [f for f in csvs if f.stat().st_size > 1024 * 100]
    if large_csvs:
        parent_dirs = {f.parent for f in large_csvs}
        if len(parent_dirs) == 1:
            return parent_dirs.pop()
        return root

    if archives:
        biggest = max(archives, key=lambda f: f.stat().st_size)
        return biggest

    return None


# ---------------------------------------------------------------------------
# Training (in-process with progress)
# ---------------------------------------------------------------------------

def run_training(
    exports: list[Path],
    output: Path,
    config_path: Path | None,
    *,
    mem_reserve_frac: float = DEFAULT_MEM_RESERVE_FRAC,
    mem_max_frac: float = DEFAULT_MEM_MAX_FRAC,
    max_csv_files: int = DEFAULT_MAX_CSV_FILES,
    max_csv_mb: int = DEFAULT_MAX_CSV_MB,
    max_feature_rows: int = DEFAULT_MAX_FEATURE_ROWS,
    max_metric_series: int = DEFAULT_MAX_METRIC_SERIES,
    max_samples_per_series: int = DEFAULT_MAX_SAMPLES_PER_SERIES,
    include_ext: set[str] | None = None,
    verbose: bool = False,
):
    import pandas as pd
    from chaosgen.config.settings import load_settings
    from chaosgen.ingestion.export_loader import ExportLoader, scan_export_inventory
    from chaosgen.ml.anomaly_detector import AnomalyDetector
    from chaosgen.ml.canonical_features import (
        CANONICAL_SCHEMA_VERSION,
        apply_canonical_features,
    )
    from chaosgen.ml.cluster_labels import ClusterLabelStore
    from chaosgen.ml.feature_engineering import FeatureEngineer

    ext_set = include_ext if include_ext is not None else DEFAULT_INCLUDE_EXT
    settings = load_settings(config_path)
    fe = FeatureEngineer(settings=settings.features)

    total = len(exports)
    total_start = time.perf_counter()
    cumulative_samples = 0
    exports_used = 0

    mem_total_bytes, mem_avail_bytes = _get_phys_mem_total_avail_bytes()
    reserve_bytes = int(mem_total_bytes * mem_reserve_frac)
    bytes_cap = int(mem_total_bytes * mem_max_frac)
    max_csv_bytes = max_csv_mb * 1024 * 1024

    combined: pd.DataFrame | None = None

    _log(
        f"\n[MEM] total {_fmt_bytes(mem_total_bytes)} | "
        f"avail {_fmt_bytes(mem_avail_bytes)} | "
        f"reserve {_fmt_bytes(reserve_bytes)} ({int(mem_reserve_frac * 100)}%) | "
        f"feature budget {_fmt_bytes(bytes_cap)} ({int(mem_max_frac * 100)}%)"
    )
    _log(
        f"[LIM] max {max_csv_files} CSV(s)/export, "
        f"{max_csv_mb} MB/export, {max_feature_rows:,} feature rows, "
        f"{max_metric_series} series/export, {max_samples_per_series} samples/series\n"
    )

    _log(f"\n{'='*60}")
    _log(f"  AUTO-TRAIN -- {total} export(s) -> {output.name}")
    _log(f"{'='*60}\n")

    for idx, export_path in enumerate(exports, 1):
        _, mem_avail_bytes = _get_phys_mem_total_avail_bytes()
        if mem_avail_bytes < reserve_bytes:
            _log(
                f"  [!] Available RAM {_fmt_bytes(mem_avail_bytes)} "
                f"< reserve {_fmt_bytes(reserve_bytes)}; stopping ingest."
            )
            break

        elapsed_total = time.perf_counter() - total_start
        if idx > 1:
            avg_per_export = elapsed_total / (idx - 1)
            eta = avg_per_export * (total - idx + 1)
            eta_str = f"ETA {_fmt_duration(eta)}"
        else:
            eta_str = "ETA calculating..."

        progress = f"[{idx}/{total}]"
        bar_width = 30
        filled = int(bar_width * idx / total)
        bar = "#" * filled + "-" * (bar_width - filled)

        _log(f"  {progress} |{bar}| {eta_str}")
        _log(f"       Loading: {export_path.name}")
        _log(f"       RAM avail: {_fmt_bytes(mem_avail_bytes)}")

        if verbose and export_path.is_dir():
            inv = scan_export_inventory(
                export_path,
                ext_set,
                max_files=max_csv_files,
                max_bytes=max_csv_bytes,
            )
            ext_summary = ", ".join(f"{k}:{v}" for k, v in sorted(inv["by_ext"].items()))
            _log(
                f"       [verbose] inventory: {inv['kept_count']} kept, "
                f"{inv['noise_count']} noise, loading {inv['load_count']} "
                f"(cap drops {inv['capped_count']})"
            )
            if ext_summary:
                _log(f"       [verbose] by ext: {ext_summary}")
            for name in [p.name for p in inv["noise"][:8]]:
                _log(f"       [verbose]   noise skip: {name}")
            if inv["noise_count"] > 8:
                _log(f"       [verbose]   ... and {inv['noise_count'] - 8} more noise files")
            for name in [p.name for p in inv["limited"][:8]]:
                _log(f"       [verbose]   will load: {name}")
            if inv["load_count"] > 8:
                _log(f"       [verbose]   ... and {inv['load_count'] - 8} more files")

        t0 = time.perf_counter()
        try:
            loader = ExportLoader.resolve_bundle(export_path)
            loader.max_csv_files = max_csv_files
            loader.max_csv_total_bytes = max_csv_bytes
            loader.include_extensions = ext_set
            dataset = loader.load()
        except Exception as e:
            _log(f"       [!] SKIP (load error): {e}")
            gc.collect()
            continue

        samples = dataset.total_samples
        series = len(dataset.metrics)
        logs = len(dataset.logs)
        cumulative_samples += samples

        _log(f"       Series: {series} | Samples: {samples} | Logs: {logs}")

        if series > max_metric_series or samples > max_metric_series * max_samples_per_series:
            dataset = _trim_dataset(
                dataset,
                max_metric_series=max_metric_series,
                max_samples_per_series=max_samples_per_series,
            )
            _log(
                f"       After trim: {len(dataset.metrics)} series, "
                f"{dataset.total_samples:,} samples"
            )

        try:
            features = fe.transform(dataset)
            features = apply_canonical_features(features, settings.features)
        except Exception as e:
            _log(f"       [!] SKIP (feature error): {e}")
            del dataset
            gc.collect()
            continue
        del dataset
        gc.collect()

        if features.empty:
            _log("       [!] No features extracted (empty after transform)")
            del features
            gc.collect()
            dt = time.perf_counter() - t0
            _log(f"       Done in {_fmt_duration(dt)} | Cumulative samples: {cumulative_samples:,}\n")
            continue

        frame_bytes_est = int(features.memory_usage(deep=False).sum())
        combined_bytes_est = (
            int(combined.memory_usage(deep=False).sum()) if combined is not None else 0
        )
        next_kept = combined_bytes_est + frame_bytes_est

        _log(
            f"       Features: {features.shape[0]} rows x {features.shape[1]} cols "
            f"(est {_fmt_bytes(frame_bytes_est)})"
        )

        if next_kept > bytes_cap:
            _log(
                f"       [!] Feature budget exceeded "
                f"(~{_fmt_bytes(next_kept)} > {_fmt_bytes(bytes_cap)}); stopping ingest."
            )
            del features
            gc.collect()
            break

        combined = _merge_features(
            combined,
            features,
            max_feature_rows=max_feature_rows,
        )
        exports_used += 1
        del features
        gc.collect()

        dt = time.perf_counter() - t0
        _log(
            f"       Done in {_fmt_duration(dt)} | "
            f"Merged rows: {len(combined):,} | Cumulative samples: {cumulative_samples:,}\n"
        )

    if combined is None or combined.empty:
        _log("\n  [X] No feature data extracted. Aborting.")
        sys.exit(1)

    _log(f"  Training on {combined.shape[0]:,} samples x {combined.shape[1]} features\n")

    _log("  Fitting IsolationForest + KMeans...")
    t_fit = time.perf_counter()
    detector = AnomalyDetector(settings=settings.anomaly)
    if settings.features.canonical_enabled:
        detector.canonical_schema_version = CANONICAL_SCHEMA_VERSION
    detector.fit(combined)
    fit_time = time.perf_counter() - t_fit
    _log(f"  Model fit in {_fmt_duration(fit_time)}")

    clusters = []
    try:
        detected = detector.detect(combined)
        if isinstance(detected, list):
            clusters = detected
    except Exception as e:
        _log(f"  [!] KMeans detect pass: {e}")

    output.parent.mkdir(parents=True, exist_ok=True)
    detector.save_model(str(output))
    _log(f"\n  [OK] Model saved: {output}")

    cluster_ids = [c.cluster_id for c in clusters]
    if not cluster_ids and getattr(detector, "last_chosen_k", None):
        cluster_ids = list(range(int(detector.last_chosen_k)))
    if cluster_ids:
        store = ClusterLabelStore.sidecar_for_model(str(output))
        label_path = store.write_stub(cluster_ids, model_path=str(output))
        _log(f"  [OK] Cluster label stub: {label_path}")

    total_elapsed = time.perf_counter() - total_start
    _log(f"\n{'='*60}")
    _log(f"  COMPLETE -- {_fmt_duration(total_elapsed)} elapsed")
    _log(f"  Exports processed: {exports_used}/{total}")
    _log(f"  Total samples ingested: {cumulative_samples:,}")
    _log(f"  Final feature matrix: {combined.shape[0]:,} x {combined.shape[1]}")
    _log(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Auto-discover datasets and train a merged ChaosGen model.")
    parser.add_argument(
        "--datasets-root", type=Path, default=DEFAULT_DATASETS_ROOT,
        help=f"Root folder containing extracted public datasets (default: {DEFAULT_DATASETS_ROOT})",
    )
    parser.add_argument(
        "--export-path", type=Path, action="append", default=None,
        help="Train a single dataset folder (repeatable). Skips auto-discovery.",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Output model path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--max-exports", type=int, default=DEFAULT_MAX_EXPORTS,
        help=f"Maximum dataset folders per run (default: {DEFAULT_MAX_EXPORTS})",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to settings.yaml (optional)",
    )
    parser.add_argument(
        "--include-ext", type=str, default=None,
        help="Comma-separated extensions (default: .csv,.csv.gz,.json,.log,.txt). Noise files still filtered.",
    )
    parser.add_argument(
        "--mem-reserve-frac", type=float, default=DEFAULT_MEM_RESERVE_FRAC,
        help=f"Keep this fraction of RAM free (default: {DEFAULT_MEM_RESERVE_FRAC}).",
    )
    parser.add_argument(
        "--mem-max-frac", type=float, default=DEFAULT_MEM_MAX_FRAC,
        help=f"Max fraction of total RAM for merged features (default: {DEFAULT_MEM_MAX_FRAC}).",
    )
    parser.add_argument(
        "--max-csv-files", type=int, default=DEFAULT_MAX_CSV_FILES,
        help=f"Max CSV files loaded per export folder (default: {DEFAULT_MAX_CSV_FILES}).",
    )
    parser.add_argument(
        "--max-csv-mb", type=int, default=DEFAULT_MAX_CSV_MB,
        help=f"Max CSV megabytes loaded per export folder (default: {DEFAULT_MAX_CSV_MB}).",
    )
    parser.add_argument(
        "--max-feature-rows", type=int, default=DEFAULT_MAX_FEATURE_ROWS,
        help=f"Cap merged feature rows (default: {DEFAULT_MAX_FEATURE_ROWS:,}).",
    )
    parser.add_argument(
        "--max-metric-series", type=int, default=DEFAULT_MAX_METRIC_SERIES,
        help=f"Max metric series per export before feature engineering (default: {DEFAULT_MAX_METRIC_SERIES}).",
    )
    parser.add_argument(
        "--max-samples-per-series", type=int, default=DEFAULT_MAX_SAMPLES_PER_SERIES,
        help=f"Max samples per series after trim (default: {DEFAULT_MAX_SAMPLES_PER_SERIES}).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List discovered exports without training",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show per-export file inventory, noise skips, and DEBUG ingest logs",
    )
    parser.add_argument(
        "--no-lock", action="store_true",
        help="Skip single-instance lock (used by train_each_dataset.py batch runner)",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger("chaosgen.ingestion").setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)

    include_ext = _parse_extensions(args.include_ext) if args.include_ext else None

    if args.export_path:
        exports = [p.resolve() for p in args.export_path]
        for p in exports:
            if not p.exists():
                logger.error("Export path not found: %s", p)
                sys.exit(1)
        logger.info("Single export mode: %d path(s)", len(exports))
    else:
        logger.info("Scanning: %s", args.datasets_root)
        if include_ext:
            logger.info("Include extensions: %s", include_ext)
        exports = discover_exports(args.datasets_root, args.max_exports, include_ext)

        if not exports:
            logger.error("No trainable exports found in %s", args.datasets_root)
            sys.exit(1)

        logger.info("Discovered %d trainable export(s):", len(exports))
        for i, e in enumerate(exports, 1):
            logger.info("  %d. %s (%s)", i, e, "dir" if e.is_dir() else f"{e.stat().st_size / 1e6:.1f} MB")

    if args.dry_run:
        logger.info("Dry run -- not training.")
        if args.verbose:
            ext_set = include_ext if include_ext is not None else DEFAULT_INCLUDE_EXT
            from chaosgen.ingestion.export_loader import scan_export_inventory
            for export in exports:
                if not export.is_dir():
                    continue
                inv = scan_export_inventory(export, ext_set)
                logger.info(
                    "  %s -> kept=%d noise=%d by_ext=%s",
                    export.name,
                    inv["kept_count"],
                    inv["noise_count"],
                    inv["by_ext"],
                )
        return

    lock_handle = None
    if not args.no_lock:
        lock_handle = _acquire_single_instance_lock()
    try:
        run_training(
            exports,
            args.output,
            args.config,
            mem_reserve_frac=args.mem_reserve_frac,
            mem_max_frac=args.mem_max_frac,
            max_csv_files=args.max_csv_files,
            max_csv_mb=args.max_csv_mb,
            max_feature_rows=args.max_feature_rows,
            max_metric_series=args.max_metric_series,
            max_samples_per_series=args.max_samples_per_series,
            include_ext=include_ext,
            verbose=args.verbose,
        )
    finally:
        if lock_handle is not None:
            try:
                import msvcrt
                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            lock_handle.close()
            try:
                LOCK_FILE.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    main()
