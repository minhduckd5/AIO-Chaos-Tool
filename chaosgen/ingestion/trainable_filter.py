"""
Filter trainable telemetry files from public dataset trees.

Keeps metrics/logs (csv, json, log, txt) and drops config, labels, docs, traces, etc.
"""
from __future__ import annotations

import re
from pathlib import Path

# Extensions the trainer may ingest (metrics and/or logs).
DEFAULT_TRAINABLE_EXTENSIONS = frozenset({".csv", ".csv.gz", ".json", ".log", ".txt"})

# Directory names to skip while walking dataset trees.
SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "scripts", "code", "src", "docs", "figures", "analysis",
    "ml-pipeline", "chaos-experiments", "test", "tests",
    "__macosx",
})

# Filename fragments that indicate non-telemetry noise.
NOISE_NAME_FRAGMENTS = (
    "readme", "license", "changelog", "contributing", "authors", "copying",
    "requirements", "package-lock", "package.json", "setup.py", "setup.cfg",
    "dockerfile", "docker-compose", "makefile", "cmakelists",
    "swagger", "openapi", "tsconfig", "eslint", "prettier",
    "fault_list", "inject_time", "ground_truth", "groundtruth",
    "anomaly_label", "label.csv", "labels.csv", "label.json",
    "experiment", "scenario", "catalog", "notebook", "tutorial",
    "zipkin", "callgraph", "call_graph",
    "node_modules", ".ipynb", "manifest",
)

# Exact / suffix trace dump names (avoid matching prometheus metrics like mspan_*).
TRACE_DUMP_NAMES = frozenset({
    "traces.csv", "trace.csv", "spans.json", "span.json", "callgraph.json",
})

# Standalone meta/config JSON names (Prom/Loki bundle meta handled elsewhere).
NOISE_JSON_NAMES = frozenset({
    "package.json", "tsconfig.json", "manifest.json", "metadata.json",
    "meta.json", "config.json", "settings.json", "composer.json",
})


def matches_extension(path: Path, include_ext: set[str]) -> bool:
    name = path.name.lower()
    for ext in sorted(include_ext, key=len, reverse=True):
        if name.endswith(ext):
            return True
    return False


def is_noise_filename(name: str) -> bool:
    lower = name.lower()
    if lower.startswith("._"):
        return True
    if lower in NOISE_JSON_NAMES or lower in TRACE_DUMP_NAMES:
        return True
    return any(frag in lower for frag in NOISE_NAME_FRAGMENTS)


def is_noise_path(path: Path) -> bool:
    if not path.is_file():
        return True
    if is_noise_filename(path.name):
        return True
    return any(part.lower() in SKIP_DIRS for part in path.parts)


def classify_json_payload(payload: object) -> str:
    """
    Classify JSON as prometheus metrics, loki logs, or noise.

    Returns: 'prometheus', 'loki', or 'noise'.
    """
    if not isinstance(payload, dict):
        return "noise"

    # Kubernetes / API manifests
    if "apiVersion" in payload and "kind" in payload:
        return "noise"

    # Prometheus query_range API
    if payload.get("status") == "success":
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("result"), list):
            result = data["result"]
            if not result:
                return "noise"
            first = result[0]
            if isinstance(first, dict):
                if "metric" in first and "values" in first:
                    return "prometheus"
                if "stream" in first and "values" in first:
                    return "loki"

    # Loki export wrapper { "result": [ { "stream", "values" } ] }
    raw = payload.get("result")
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict) and "stream" in first and "values" in first:
            return "loki"

    # RCAEval / compact metric map: { "series_name": [[ts, value], ...], ... }
    values = list(payload.values())
    if values and all(isinstance(v, list) and v for v in values):
        sample = values[0][0]
        if (
            isinstance(sample, (list, tuple))
            and len(sample) >= 2
            and isinstance(sample[0], (int, float))
        ):
            return "timeseries_dict"

    return "noise"


_LOG_LEVEL_RE = re.compile(
    r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL|PANIC|TRACE)\b",
    re.IGNORECASE,
)


def infer_log_level(line: str) -> str | None:
    match = _LOG_LEVEL_RE.search(line)
    if match:
        return match.group(1).upper()
    return None
