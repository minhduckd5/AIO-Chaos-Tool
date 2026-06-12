"""
Load offline observability exports (from fetch-observability-data.ps1) into TelemetryDataset.

Supports:
  - prometheus/*.json  — native Prometheus query_range API responses
  - loki_logs.json     — wrapped export format (meta + result[])
  - loki_meta.json     — optional collection window metadata
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from chaosgen.ingestion.loki_client import LokiClient
from chaosgen.ingestion.prometheus_client import PrometheusClient
from chaosgen.schemas.telemetry import LogStream, TelemetryDataset, TimeSeries

logger = logging.getLogger(__name__)


class ExportLoader:
    """Load a single export bundle directory into TelemetryDataset."""

    def __init__(self, export_root: str | Path):
        self.export_root = Path(export_root)
        if not self.export_root.exists():
            raise FileNotFoundError(f"Export directory not found: {self.export_root}")

    @classmethod
    def resolve_bundle(cls, path: str | Path) -> "ExportLoader":
        """
        Accept either an export bundle (contains prometheus/) or a parent exports/
        folder — uses the most recently modified bundle subdirectory.
        """
        root = Path(path)
        if (root / "prometheus").is_dir():
            return cls(root)
        if root.is_dir():
            candidates = sorted(
                [p for p in root.iterdir() if p.is_dir() and (p / "prometheus").is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                logger.info("Resolved latest export bundle: %s", candidates[0])
                return cls(candidates[0])
        raise FileNotFoundError(
            f"No export bundle found under {root}. Expected prometheus/ and loki_logs.json."
        )

    def load(self) -> TelemetryDataset:
        metrics = self._load_prometheus_dir(self.export_root / "prometheus")
        logs = self._load_loki_export(self.export_root / "loki_logs.json")
        start, end = self._resolve_window()

        logger.info(
            "Loaded export %s: %d metric series, %d log streams, %d log lines",
            self.export_root.name,
            len(metrics),
            len(logs),
            sum(len(s.entries) for s in logs),
        )
        return TelemetryDataset(
            metrics=metrics,
            logs=logs,
            collection_start=start,
            collection_end=end,
            source_cluster=self.export_root.name,
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
            payload = self._read_json(path)
            if payload.get("status") != "success":
                logger.warning("Skipping %s — status != success", path.name)
                continue
            query_name = path.stem
            parsed = PrometheusClient._parse_range_results(
                payload["data"]["result"], query_name
            )
            for ts in parsed:
                ts.metric_name = f"export__{query_name}__{ts.metric_name}"
            series.extend(parsed)
        return series

    def _load_loki_export(self, loki_path: Path) -> list[LogStream]:
        if not loki_path.exists():
            logger.warning("No loki_logs.json in export: %s", loki_path)
            return []

        payload = self._read_json(loki_path)
        raw_results = payload.get("result", [])

        # Native Loki API shape (future-compatible)
        if raw_results and "stream" in raw_results[0] and "values" in raw_results[0]:
            if "chunk" not in raw_results[0]:
                return LokiClient._parse_streams(raw_results)

        # Wrapped export from fetch-observability-data.ps1
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

    @staticmethod
    def _read_json(path: Path) -> dict:
        text = path.read_text(encoding="utf-8-sig")
        return json.loads(text)
