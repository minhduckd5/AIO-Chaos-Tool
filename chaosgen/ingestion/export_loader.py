"""
Load offline observability exports into TelemetryDataset.

Supports:
  - prometheus/*.json  — native Prometheus query_range API responses
  - loki_logs.json     — wrapped export format (meta + result[])
  - loki_meta.json     — optional collection window metadata
  - *.csv / *.csv.gz   — wide or long numeric time series (trainer / GUI)
  - *.json             — Prometheus range or Loki log exports (noise filtered)
  - *.log / *.txt      — plain log lines (capped; noise filtered)
  - .zip / .tar / .tar.gz — same contents without extracting to disk
  - nested archives (tar.gz inside tar, zip inside zip) up to 4 levels
"""

from __future__ import annotations

import io
import json
import logging
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from chaosgen.ingestion.trainable_filter import (
    DEFAULT_TRAINABLE_EXTENSIONS,
    SKIP_DIRS,
    classify_json_payload,
    infer_log_level,
    is_noise_filename,
    is_noise_path,
    matches_extension,
    TRACE_DUMP_NAMES,
)
from chaosgen.ingestion.loki_client import LokiClient
from chaosgen.ingestion.prometheus_client import PrometheusClient
from chaosgen.schemas.telemetry import LogStream, MetricSample, TelemetryDataset, TimeSeries

logger = logging.getLogger(__name__)

# --- START MODIFICATION ---
# CSV / archive ingest for train-model (and GUI export source)
_TIME_ALIASES = {
    "timestamp", "time", "ts", "datetime", "date", "time_stamp",
    "unix_time", "unixtime", "ds", "t", "event_time", "collect_time",
}
_VALUE_ALIASES = {"value", "val", "v", "y_value", "metric_value"}
_METRIC_ALIASES = {"metric", "metric_name", "name", "kpi", "series", "indicator"}
_SKIP_COLUMNS = {
    "label", "labels", "y", "anomaly", "is_anomaly", "fault", "attack",
    "class", "target", "ground_truth", "groundtruth", "rca", "root_cause",
    "rootcause", "inject", "injection", "id", "index", "unnamed: 0",
}
_ARCHIVE_SUFFIXES = (".tar.gz", ".tar.bz2", ".tgz", ".zip", ".tar")
_MAX_ARCHIVE_DEPTH = 4
_SYNTHETIC_STEP_SECONDS = 60.0
_DEFAULT_MAX_LOG_LINES = 10_000
_TRAINABLE_SCAN_MAX_DEPTH = 6
# --- END MODIFICATION ---


class ExportLoader:
    """Load a lab Prom/Loki bundle, CSV table(s), or a compressed archive."""

    def __init__(
        self,
        export_root: str | Path,
        kind: str = "bundle",
        *,
        max_csv_files: int | None = None,
        max_csv_total_bytes: int | None = None,
        max_log_lines: int = _DEFAULT_MAX_LOG_LINES,
        include_extensions: set[str] | None = None,
    ):
        self.export_root = Path(export_root)
        if not self.export_root.exists():
            raise FileNotFoundError(f"Export path not found: {self.export_root}")
        self.kind = kind
        self.max_csv_files = max_csv_files
        self.max_csv_total_bytes = max_csv_total_bytes
        self.max_log_lines = max_log_lines
        self.include_extensions = (
            set(include_extensions) if include_extensions else set(DEFAULT_TRAINABLE_EXTENSIONS)
        )

    @classmethod
    def resolve_bundle(cls, path: str | Path) -> "ExportLoader":
        """
        Accept a Prom/Loki bundle, a CSV file, a directory of CSVs, an archive,
        or a parent exports/ folder (latest bundle subdirectory).
        """
        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(f"Export path not found: {root}")

        # MODIFIED: CSV file or compressed dump (no extract required)
        if root.is_file():
            name = root.name.lower()
            if is_noise_path(root):
                raise FileNotFoundError(f"Noise file skipped: {root}")
            if name.endswith(".csv") or name.endswith(".csv.gz"):
                return cls(root, kind="csv")
            if name.endswith(".json"):
                return cls(root, kind="json")
            if name.endswith(".log") or name.endswith(".txt"):
                return cls(root, kind="log")
            if any(name.endswith(suf) for suf in _ARCHIVE_SUFFIXES):
                return cls(root, kind="archive")
            raise FileNotFoundError(
                f"Unsupported export file {root}. Use a Prom/Loki bundle dir, "
                f"CSV/JSON/log, or zip/tar archive."
            )

        if (root / "prometheus").is_dir():
            return cls(root, kind="bundle")

        trainable = _discover_trainable_files(root)
        if trainable:
            logger.info(
                "Resolved mixed export directory (%d files): %s",
                len(trainable),
                root,
            )
            return cls(root, kind="mixed_dir")

        candidates = sorted(
            [p for p in root.iterdir() if p.is_dir() and (p / "prometheus").is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            logger.info("Resolved latest export bundle: %s", candidates[0])
            return cls(candidates[0], kind="bundle")
        raise FileNotFoundError(
            f"No export bundle or CSV found under {root}. "
            f"Expected prometheus/, *.csv, or a zip/tar archive."
        )

    def load(self) -> TelemetryDataset:
        # MODIFIED: dispatch CSV / archive / native bundle
        if self.kind == "csv":
            metrics = self._load_csv_paths([self.export_root])
            logs: list[LogStream] = []
        elif self.kind == "json":
            metrics, logs = self._load_json_file(self.export_root)
        elif self.kind == "log":
            logs = self._load_log_text_file(self.export_root)
            metrics = []
        elif self.kind in {"csv_dir", "mixed_dir"}:
            paths = _discover_trainable_files(self.export_root, self.include_extensions)
            paths = _limit_trainable_paths(
                paths,
                self.max_csv_files,
                self.max_csv_total_bytes,
            )
            metrics, logs = self._load_trainable_paths(paths)
        elif self.kind == "archive":
            metrics, logs = self._load_archive(self.export_root)
        else:
            metrics = self._load_prometheus_dir(self.export_root / "prometheus")
            logs = self._load_loki_export(self.export_root / "loki_logs.json")

        start, end = self._window_from_metrics(metrics)
        logger.info(
            "Loaded export %s (%s): %d metric series, %d log streams, %d samples",
            self.export_root.name,
            self.kind,
            len(metrics),
            len(logs),
            sum(len(ts.samples) for ts in metrics),
        )
        return TelemetryDataset(
            metrics=metrics,
            logs=logs,
            collection_start=start,
            collection_end=end,
            source_cluster=self.export_root.stem,
        )

    def _window_from_metrics(self, metrics: list[TimeSeries]) -> tuple[datetime, datetime]:
        if self.kind == "bundle":
            return self._resolve_window()
        stamps: list[float] = []
        for ts in metrics:
            stamps.extend(ts.timestamps)
        if not stamps:
            now = datetime.now(tz=timezone.utc)
            return now, now
        return (
            datetime.fromtimestamp(min(stamps), tz=timezone.utc),
            datetime.fromtimestamp(max(stamps), tz=timezone.utc),
        )

    def _resolve_window(self) -> tuple[datetime, datetime]:
        for meta_path in (self.export_root / "loki_meta.json", self.export_root / "loki_logs.json"):
            if not meta_path.exists():
                continue
            meta = self._read_json(meta_path)
            block = meta.get("meta", meta)
            start_s = block.get("start_utc")
            end_s = block.get("end_utc")
            if start_s and end_s:
                return (
                    datetime.fromisoformat(start_s.replace("Z", "+00:00")),
                    datetime.fromisoformat(end_s.replace("Z", "+00:00")),
                )
        return (
            datetime.now(tz=timezone.utc),
            datetime.now(tz=timezone.utc),
        )

    def _load_prometheus_dir(self, prom_dir: Path) -> list[TimeSeries]:
        if not prom_dir.is_dir():
            logger.warning("No prometheus/ directory in export: %s", prom_dir)
            return []

        series: list[TimeSeries] = []
        for path in sorted(prom_dir.glob("*.json")):
            series.extend(self._prometheus_payload_to_series(self._read_json(path), path.stem))
        return series

    def _prometheus_payload_to_series(self, payload: dict, query_name: str) -> list[TimeSeries]:
        if payload.get("status") != "success":
            logger.warning("Skipping %s — status != success", query_name)
            return []
        parsed = PrometheusClient._parse_range_results(
            payload["data"]["result"], query_name
        )
        for ts in parsed:
            ts.metric_name = f"export__{query_name}__{ts.metric_name}"
        return parsed

    def _timeseries_dict_to_series(self, payload: dict, source_name: str) -> list[TimeSeries]:
        """Parse { metric_name: [[timestamp, value], ...] } exports (RCAEval, etc.)."""
        series: list[TimeSeries] = []
        for metric_name, points in payload.items():
            if not isinstance(points, list) or not points:
                continue
            samples: list[MetricSample] = []
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    ts_val = float(point[0])
                    value = float(point[1])
                except (TypeError, ValueError):
                    continue
                if ts_val > 1e14:
                    ts_val /= 1e9
                elif ts_val > 1e11:
                    ts_val /= 1e3
                samples.append(MetricSample(timestamp=ts_val, value=value))
            if samples:
                series.append(
                    TimeSeries(
                        metric_name=f"json__{source_name}__{metric_name}",
                        samples=samples,
                    )
                )
        return series

    def _load_loki_export(self, loki_path: Path) -> list[LogStream]:
        if not loki_path.exists():
            logger.warning("No loki_logs.json in export: %s", loki_path)
            return []
        return self._loki_payload_to_streams(self._read_json(loki_path))

    def _loki_payload_to_streams(self, payload: dict) -> list[LogStream]:
        raw_results = payload.get("result", [])
        if raw_results and "stream" in raw_results[0] and "values" in raw_results[0]:
            if "chunk" not in raw_results[0]:
                return LokiClient._parse_streams(raw_results)

        merged: dict[str, LogStream] = {}
        for item in raw_results:
            labels = item.get("stream", {})
            key = json.dumps(labels, sort_keys=True)
            entries = LokiClient._parse_entries([{"stream": labels, "values": item.get("values", [])}])
            if key in merged:
                merged[key].entries.extend(entries)
            else:
                merged[key] = LogStream(stream_labels=labels, entries=entries)
        return list(merged.values())

    def _load_csv_paths(self, paths: Iterable[Path]) -> list[TimeSeries]:
        series: list[TimeSeries] = []
        for path in paths:
            try:
                df = pd.read_csv(path, encoding="utf-8-sig")
            except Exception as exc:
                logger.warning("Skipping unreadable CSV %s: %s", path, exc)
                continue
            series.extend(dataframe_to_timeseries(df, source_prefix=path.stem))
        return series

    def _load_json_file(self, path: Path) -> tuple[list[TimeSeries], list[LogStream]]:
        try:
            payload = self._read_json(path)
        except Exception as exc:
            logger.warning("Skipping unreadable JSON %s: %s", path, exc)
            return [], []
        kind = classify_json_payload(payload)
        if kind == "prometheus":
            return self._prometheus_payload_to_series(payload, path.stem), []
        if kind == "timeseries_dict":
            return self._timeseries_dict_to_series(payload, path.stem), []
        if kind == "loki":
            return [], self._loki_payload_to_streams(payload)
        logger.info("Skipping noise JSON (unknown schema): %s", path.name)
        return [], []

    def _load_log_text_file(self, path: Path) -> list[LogStream]:
        from chaosgen.schemas.telemetry import LogEntry

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Skipping unreadable log %s: %s", path, exc)
            return []

        lines = text.splitlines()[: self.max_log_lines]
        if not lines:
            return []

        base_ts = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        entries: list[LogEntry] = []
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            entries.append(
                LogEntry(
                    timestamp=base_ts + i * _SYNTHETIC_STEP_SECONDS,
                    message=line,
                    level=infer_log_level(line),
                    labels={"source": path.stem},
                )
            )
        if not entries:
            return []
        return [LogStream(stream_labels={"source": path.stem}, entries=entries)]

    def _load_trainable_paths(
        self, paths: Iterable[Path]
    ) -> tuple[list[TimeSeries], list[LogStream]]:
        metrics: list[TimeSeries] = []
        logs: list[LogStream] = []
        for path in paths:
            name = path.name.lower()
            if name.endswith(".csv") or name.endswith(".csv.gz"):
                metrics.extend(self._load_csv_paths([path]))
            elif name.endswith(".json"):
                m, lg = self._load_json_file(path)
                metrics.extend(m)
                logs.extend(lg)
            elif name.endswith(".log") or name.endswith(".txt"):
                logs.extend(self._load_log_text_file(path))
        return metrics, logs

    def _load_archive(self, archive_path: Path) -> tuple[list[TimeSeries], list[LogStream]]:
        metrics: list[TimeSeries] = []
        logs: list[LogStream] = []
        for member_name, raw in _iter_archive_members(archive_path):
            lower = member_name.replace("\\", "/").lower()
            if _archive_suffix(lower):
                logger.warning("Skipping archive member past depth cap: %s", member_name)
                continue
            if lower.endswith(".csv") or lower.endswith(".csv.gz"):
                try:
                    df = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
                except Exception as exc:
                    logger.warning("Skipping CSV in archive %s: %s", member_name, exc)
                    continue
                stem = Path(member_name.split("!/")[-1]).stem
                if is_noise_filename(stem):
                    continue
                metrics.extend(dataframe_to_timeseries(df, source_prefix=stem))
                continue
            if not lower.endswith(".json"):
                continue
            try:
                payload = json.loads(raw.decode("utf-8-sig"))
            except Exception as exc:
                logger.warning("Skipping JSON in archive %s: %s", member_name, exc)
                continue
            base = Path(member_name.split("!/")[-1]).stem
            if is_noise_filename(base):
                continue
            json_kind = classify_json_payload(payload)
            if json_kind == "prometheus":
                metrics.extend(self._prometheus_payload_to_series(payload, base))
            elif json_kind == "timeseries_dict":
                metrics.extend(self._timeseries_dict_to_series(payload, base))
            elif json_kind == "loki":
                logs.extend(self._loki_payload_to_streams(payload))
        return metrics, logs

    @staticmethod
    def _read_json(path: Path) -> dict:
        text = path.read_text(encoding="utf-8-sig")
        return json.loads(text)


def _iter_files_under(root: Path, max_depth: int, _depth: int = 0) -> Iterable[Path]:
    """Yield files up to max_depth below root (skip noisy directory names)."""
    try:
        for child in sorted(root.iterdir()):
            if child.is_file():
                yield child
            elif child.is_dir() and _depth < max_depth and child.name.lower() not in SKIP_DIRS:
                yield from _iter_files_under(child, max_depth, _depth + 1)
    except PermissionError:
        pass


def _discover_trainable_files(
    root: Path,
    include_ext: set[str] | None = None,
    *,
    max_depth: int = _TRAINABLE_SCAN_MAX_DEPTH,
) -> list[Path]:
    """Discover csv/json/log/txt under root up to max_depth, skipping noise paths."""
    exts = include_ext or set(DEFAULT_TRAINABLE_EXTENSIONS)
    found: list[Path] = []
    seen: set[Path] = set()

    for path in _iter_files_under(root, max_depth):
        if path in seen:
            continue
        if not matches_extension(path, exts):
            continue
        if is_noise_path(path):
            logger.debug("Skipping noise path: %s", path)
            continue
        seen.add(path)
        found.append(path)

    if found:
        logger.debug(
            "Discovered %d trainable file(s) under %s (depth<=%d)",
            len(found),
            root.name,
            max_depth,
        )
    return found


def scan_export_inventory(
    root: Path,
    include_ext: set[str] | None = None,
    *,
    max_files: int | None = None,
    max_bytes: int | None = None,
    max_depth: int = _TRAINABLE_SCAN_MAX_DEPTH,
) -> dict:
    """
    Summarize trainable vs noise files under an export folder (for --verbose).
    """
    exts = include_ext or set(DEFAULT_TRAINABLE_EXTENSIONS)
    kept: list[Path] = []
    noise: list[Path] = []

    for path in _iter_files_under(root, max_depth):
        if not matches_extension(path, exts):
            continue
        if is_noise_path(path):
            noise.append(path)
        else:
            kept.append(path)

    limited = _limit_trainable_paths(kept, max_files, max_bytes)
    by_ext: dict[str, int] = {}
    for path in kept:
        name = path.name.lower()
        ext = ".csv.gz" if name.endswith(".csv.gz") else Path(name).suffix
        by_ext[ext] = by_ext.get(ext, 0) + 1

    return {
        "kept": kept,
        "noise": noise,
        "limited": limited,
        "kept_count": len(kept),
        "noise_count": len(noise),
        "load_count": len(limited),
        "capped_count": max(0, len(kept) - len(limited)),
        "by_ext": by_ext,
    }


def _trainable_path_priority(path: Path) -> tuple[int, str]:
    """Lower sort key = load first. Prefer metrics CSVs over agent JSON blobs."""
    name = path.name.lower()
    if name == "metrics.csv":
        return (0, name)
    if name == "metrics.json":
        return (0, name)
    if name.endswith(".csv") or name.endswith(".csv.gz"):
        if name in TRACE_DUMP_NAMES:
            return (4, name)
        return (1, name)
    if name.endswith(".log") or name.endswith(".txt"):
        return (2, name)
    if name.endswith(".json"):
        if name in {"logs.json", "alert.json", "k8s_states.json", "tool_cache.json", "metadata.json"}:
            return (6, name)
        if name in TRACE_DUMP_NAMES:
            return (5, name)
        return (3, name)
    return (7, name)


def _limit_trainable_paths(
    paths: list[Path],
    max_files: int | None,
    max_bytes: int | None,
) -> list[Path]:
    """Cap file ingest so train-model cannot load thousands of files into RAM."""
    if not paths or (max_files is None and max_bytes is None):
        return paths

    paths = sorted(paths, key=_trainable_path_priority)

    selected: list[Path] = []
    total_bytes = 0
    for path in paths:
        if max_files is not None and len(selected) >= max_files:
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if max_bytes is not None and selected and total_bytes + size > max_bytes:
            break
        if max_bytes is not None and not selected and size > max_bytes:
            logger.warning(
                "Skipping file larger than byte cap (%d MB): %s",
                max_bytes // (1024 * 1024),
                path,
            )
            continue
        selected.append(path)
        total_bytes += size

    if len(selected) < len(paths):
        logger.warning(
            "Trainable ingest capped: loading %d / %d files (~%.1f MB)",
            len(selected),
            len(paths),
            total_bytes / (1024 * 1024),
        )
    return selected


# Backward-compatible alias
def _discover_csv_files(root: Path) -> list[Path]:
    return _discover_trainable_files(root, {".csv", ".csv.gz"})


def _limit_csv_paths(
    paths: list[Path],
    max_files: int | None,
    max_bytes: int | None,
) -> list[Path]:
    return _limit_trainable_paths(paths, max_files, max_bytes)


def _archive_suffix(name: str) -> str | None:
    lower = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suf in _ARCHIVE_SUFFIXES:
        if lower.endswith(suf):
            return suf
    return None


def _iter_archive_members(path: Path) -> Iterable[tuple[str, bytes]]:
    """Yield leaf files, expanding nested zip/tar/tar.gz up to _MAX_ARCHIVE_DEPTH."""
    name = path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                yield from _expand_archive_member(info.filename, zf.read(info), depth=1)
        return
    with tarfile.open(path, mode="r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            yield from _expand_archive_member(member.name, extracted.read(), depth=1)


def _expand_archive_member(
    logical_path: str, raw: bytes, depth: int
) -> Iterable[tuple[str, bytes]]:
    suf = _archive_suffix(logical_path)
    if suf is None:
        yield logical_path, raw
        return
    if depth > _MAX_ARCHIVE_DEPTH:
        logger.warning(
            "Nested archive %s exceeds depth %d; extract remaining layers.",
            logical_path,
            _MAX_ARCHIVE_DEPTH,
        )
        yield logical_path, raw
        return
    logger.info("Expanding nested archive %s (depth %d)", logical_path, depth)
    try:
        yield from _iter_bytes_archive(logical_path, raw, suf, depth)
    except Exception as exc:
        logger.warning("Failed to open nested archive %s: %s", logical_path, exc)


def _iter_bytes_archive(
    logical_path: str, raw: bytes, suf: str, depth: int
) -> Iterable[tuple[str, bytes]]:
    bio = io.BytesIO(raw)
    if suf == ".zip":
        with zipfile.ZipFile(bio) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                child = f"{logical_path}!/{info.filename}"
                yield from _expand_archive_member(child, zf.read(info), depth + 1)
        return
    if suf in {".tar.gz", ".tgz"}:
        mode = "r:gz"
    elif suf == ".tar.bz2":
        mode = "r:bz2"
    else:
        mode = "r:*"
    with tarfile.open(fileobj=bio, mode=mode) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            child = f"{logical_path}!/{member.name}"
            yield from _expand_archive_member(child, extracted.read(), depth + 1)


def dataframe_to_timeseries(df: pd.DataFrame, source_prefix: str) -> list[TimeSeries]:
    """Map a numeric CSV table to TimeSeries (wide or long format)."""
    if df.empty:
        return []

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    time_col = _find_alias_column(df.columns, _TIME_ALIASES)
    index = _build_time_index(df, time_col)

    long_metric = _find_alias_column(df.columns, _METRIC_ALIASES)
    long_value = _find_alias_column(df.columns, _VALUE_ALIASES)
    if time_col and long_metric and long_value and long_metric != time_col:
        return _long_csv_to_timeseries(df, index, long_metric, long_value, source_prefix)

    series_out: list[TimeSeries] = []
    for col in df.columns:
        if col == time_col:
            continue
        if col.strip().lower() in _SKIP_COLUMNS:
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        if numeric.notna().sum() < max(3, int(len(df) * 0.5)):
            continue
        name = f"csv__{source_prefix}__{col}"
        series_out.append(_index_values_to_timeseries(name, index, numeric))
    if not series_out:
        logger.warning("CSV %s produced no numeric metric columns", source_prefix)
    return series_out


def _long_csv_to_timeseries(
    df: pd.DataFrame,
    index: pd.DatetimeIndex,
    metric_col: str,
    value_col: str,
    source_prefix: str,
) -> list[TimeSeries]:
    tmp = pd.DataFrame(
        {
            "timestamp": index,
            "metric": df[metric_col].astype(str).to_numpy(),
            "value": pd.to_numeric(df[value_col], errors="coerce").to_numpy(),
        }
    ).dropna(subset=["timestamp", "value"])
    out: list[TimeSeries] = []
    for metric_name, group in tmp.groupby("metric"):
        if str(metric_name).strip().lower() in _SKIP_COLUMNS:
            continue
        name = f"csv__{source_prefix}__{metric_name}"
        out.append(
            _index_values_to_timeseries(
                name,
                pd.DatetimeIndex(group["timestamp"]),
                group["value"],
            )
        )
    return out


def _index_values_to_timeseries(
    name: str,
    index: pd.DatetimeIndex,
    values: pd.Series,
) -> TimeSeries:
    samples: list[MetricSample] = []
    for stamp, val in zip(index, values):
        if pd.isna(val) or pd.isna(stamp):
            continue
        samples.append(MetricSample(timestamp=float(stamp.timestamp()), value=float(val)))
    return TimeSeries(metric_name=name, samples=samples)


def _find_alias_column(columns: Iterable[str], aliases: set[str]) -> str | None:
    for col in columns:
        if str(col).strip().lower() in aliases:
            return col
    return None


def _build_time_index(df: pd.DataFrame, time_col: str | None) -> pd.DatetimeIndex:
    if time_col is None:
        logger.info("CSV has no timestamp column; using synthetic %ss index", int(_SYNTHETIC_STEP_SECONDS))
        start = pd.Timestamp("2020-01-01", tz="UTC")
        return pd.DatetimeIndex(
            [start + pd.Timedelta(seconds=_SYNTHETIC_STEP_SECONDS * i) for i in range(len(df))]
        )
    raw = df[time_col]
    if pd.api.types.is_numeric_dtype(raw):
        sample = pd.to_numeric(raw, errors="coerce").dropna()
        unit = "s"
        if not sample.empty:
            peak = float(sample.abs().max())
            if peak > 1e16:
                unit = "ns"
            elif peak > 1e14:
                unit = "us"
            elif peak > 1e11:
                unit = "ms"
        parsed = pd.to_datetime(raw, unit=unit, utc=True, errors="coerce")
    else:
        parsed = pd.to_datetime(raw, utc=True, errors="coerce")
    if parsed.isna().all():
        logger.warning("Failed to parse timestamp column %s; using synthetic index", time_col)
        start = pd.Timestamp("2020-01-01", tz="UTC")
        return pd.DatetimeIndex(
            [start + pd.Timedelta(seconds=_SYNTHETIC_STEP_SECONDS * i) for i in range(len(df))]
        )
    return pd.DatetimeIndex(parsed)
