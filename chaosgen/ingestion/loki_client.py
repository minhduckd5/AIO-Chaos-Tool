import logging
import re
from typing import Any, Dict, List, Optional

import requests

from chaosgen.schemas.telemetry import LogEntry, LogStream

logger = logging.getLogger(__name__)

LOG_LEVEL_PATTERN = re.compile(
    r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL|PANIC)\b", re.IGNORECASE
)


class LokiClient:
    """Wraps the Loki HTTP API for log ingestion."""

    def __init__(self, base_url: str, timeout: int = 30, bearer_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.bearer_token = bearer_token

    def _headers(self) -> dict[str, str]:
        if not self.bearer_token:
            return {}
        return {"Authorization": f"Bearer {self.bearer_token}"}

    def query(self, logql: str, limit: int = 1000) -> List[LogEntry]:
        """Execute an instant LogQL query."""
        try:
            resp = requests.get(
                f"{self.base_url}/loki/api/v1/query",
                params={"query": logql, "limit": limit},
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                logger.warning("Loki query failed: %s", data.get("error", "unknown"))
                return []

            return self._parse_entries(data["data"]["result"])
        except requests.RequestException as e:
            logger.error("Loki connection error: %s", e)
            return []

    def query_range(
        self,
        logql: str,
        start: float,
        end: float,
        limit: int = 5000,
    ) -> List[LogStream]:
        """Execute a range LogQL query."""
        try:
            resp = requests.get(
                f"{self.base_url}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "start": int(start * 1e9),
                    "end": int(end * 1e9),
                    "limit": limit,
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                logger.warning("Loki range query failed: %s", data.get("error", "unknown"))
                return []

            return self._parse_streams(data["data"]["result"])
        except requests.RequestException as e:
            logger.error("Loki connection error: %s", e)
            return []

    def health_check(self) -> bool:
        ok, _ = self.probe()
        return ok

    def probe(self) -> tuple[bool, str]:
        try:
            resp = requests.get(f"{self.base_url}/ready", timeout=5)
            if resp.status_code == 401:
                return False, "401 Unauthorized — set Loki Token in Settings"
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}"
            return True, "OK"
        except requests.RequestException as exc:
            return False, f"unreachable ({exc})"

    @staticmethod
    def _extract_level(message: str) -> Optional[str]:
        match = LOG_LEVEL_PATTERN.search(message)
        return match.group(1).upper() if match else None

    @classmethod
    def _parse_entries(cls, results: List[Dict[str, Any]]) -> List[LogEntry]:
        entries = []
        for stream in results:
            labels = stream.get("stream", {})
            for ts_ns, msg in stream.get("values", []):
                entries.append(
                    LogEntry(
                        timestamp=int(ts_ns) / 1e9,
                        message=msg,
                        labels=labels,
                        level=cls._extract_level(msg),
                    )
                )
        return entries

    @classmethod
    def _parse_streams(cls, results: List[Dict[str, Any]]) -> List[LogStream]:
        streams = []
        for stream_data in results:
            labels = stream_data.get("stream", {})
            entries = [
                LogEntry(
                    timestamp=int(ts_ns) / 1e9,
                    message=msg,
                    labels=labels,
                    level=cls._extract_level(msg),
                )
                for ts_ns, msg in stream_data.get("values", [])
            ]
            streams.append(LogStream(stream_labels=labels, entries=entries))
        return streams
