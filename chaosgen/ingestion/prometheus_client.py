import logging
from typing import Any, Dict, List, Optional

import requests

from chaosgen.schemas.telemetry import MetricSample, TimeSeries

logger = logging.getLogger(__name__)

# Pre-built PromQL queries for SRE golden signals
GOLDEN_SIGNAL_QUERIES: Dict[str, str] = {
    "request_rate": 'sum(rate(http_requests_total[5m])) by (service)',
    "error_rate": 'sum(rate(http_requests_total{code=~"5.."}[5m])) by (service)',
    "latency_p50": 'histogram_quantile(0.5, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))',
    "latency_p95": 'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))',
    "latency_p99": 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))',
    "cpu_usage": 'sum(rate(container_cpu_usage_seconds_total[5m])) by (pod)',
    "memory_usage": 'sum(container_memory_working_set_bytes) by (pod)',
    "saturation_cpu": 'sum(rate(container_cpu_cfs_throttled_seconds_total[5m])) by (pod)',
    "network_rx_bytes": 'sum(rate(container_network_receive_bytes_total[5m])) by (pod)',
    "network_tx_bytes": 'sum(rate(container_network_transmit_bytes_total[5m])) by (pod)',
}

# When golden signals return nothing (e.g. no app scrape targets), use infra fallback baselines.
FALLBACK_INFRA_QUERIES: Dict[str, str] = {
    "up": "up",
    "node_cpu": 'rate(node_cpu_seconds_total{mode!="idle"}[5m])',
    "node_memory_avail": "node_memory_MemAvailable_bytes",
    "http_server_duration_count": "http_server_duration_count",
    "rpc_server_duration_count": "sum(rate(rpc_server_duration_count[5m])) by (service_name)",
}


class PrometheusClient:
    """Wraps the Prometheus HTTP API for metric ingestion."""

    def __init__(self, base_url: str, timeout: int = 30, bearer_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.bearer_token = bearer_token

    def _headers(self) -> dict[str, str]:
        if not self.bearer_token:
            return {}
        return {"Authorization": f"Bearer {self.bearer_token}"}

    def query(self, promql: str) -> List[MetricSample]:
        """Execute an instant PromQL query."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/query",
                params={"query": promql},
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                logger.warning("Prometheus query failed: %s", data.get("error", "unknown"))
                return []

            return self._parse_instant_results(data["data"]["result"])
        except requests.RequestException as e:
            logger.error("Prometheus connection error: %s", e)
            return []

    def query_range(
        self,
        promql: str,
        start: float,
        end: float,
        step: str = "15s",
    ) -> List[TimeSeries]:
        """Execute a range PromQL query."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/query_range",
                params={
                    "query": promql,
                    "start": start,
                    "end": end,
                    "step": step,
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                logger.warning("Prometheus range query failed: %s", data.get("error", "unknown"))
                return []

            return self._parse_range_results(data["data"]["result"], promql)
        except requests.RequestException as e:
            logger.error("Prometheus connection error: %s", e)
            return []

    def query_golden_signals(
        self, start: float, end: float, step: str = "60s"
    ) -> List[TimeSeries]:
        """Fetch all SRE golden signal metrics for a time range."""
        all_series: List[TimeSeries] = []
        for signal_name, promql in GOLDEN_SIGNAL_QUERIES.items():
            series = self.query_range(promql, start, end, step)
            for ts in series:
                ts.metric_name = f"{signal_name}__{ts.metric_name}"
            all_series.extend(series)
        return all_series

    def query_fallback_infra(
        self, start: float, end: float, step: str = "60s"
    ) -> List[TimeSeries]:
        """Infra fallback metrics when classic microservice golden signals are absent."""
        all_series: List[TimeSeries] = []
        for signal_name, promql in FALLBACK_INFRA_QUERIES.items():
            series = self.query_range(promql, start, end, step)
            for ts in series:
                ts.metric_name = f"infra__{signal_name}__{ts.metric_name}"
            all_series.extend(series)
        return all_series

    def health_check(self) -> bool:
        """Verify Prometheus is reachable."""
        ok, _ = self.probe()
        return ok

    def probe(self) -> tuple[bool, str]:
        """Reachability + auth + sample query (more reliable than /-/healthy alone)."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/query",
                params={"query": "up"},
                headers=self._headers(),
                timeout=5,
            )
            if resp.status_code == 401:
                return False, "401 Unauthorized — set Prometheus Token in Settings"
            if resp.status_code == 403:
                return False, "403 Forbidden — check Prometheus Token / RBAC"
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "success":
                return False, data.get("error", "query failed")
            series = len(data.get("data", {}).get("result", []))
            if series == 0:
                return True, "OK (reachable, but 'up' returned 0 series — check scrape targets)"
            return True, f"OK ({series} targets in 'up' query)"
        except requests.RequestException as exc:
            try:
                ping = requests.get(f"{self.base_url}/-/healthy", timeout=5)
                if ping.status_code == 200:
                    return False, f"reachable but query failed: {exc}"
            except requests.RequestException:
                pass
            return False, f"unreachable ({exc})"

    @staticmethod
    def _parse_instant_results(results: List[Dict[str, Any]]) -> List[MetricSample]:
        samples = []
        for result in results:
            ts, val = result["value"]
            samples.append(
                MetricSample(
                    timestamp=float(ts),
                    value=float(val),
                    labels=result.get("metric", {}),
                )
            )
        return samples

    @staticmethod
    def _parse_range_results(
        results: List[Dict[str, Any]], query_name: str
    ) -> List[TimeSeries]:
        series_list = []
        for result in results:
            labels = result.get("metric", {})
            metric_name = labels.pop("__name__", query_name)
            samples = [
                MetricSample(timestamp=float(ts), value=float(val), labels={})
                for ts, val in result["values"]
            ]
            series_list.append(
                TimeSeries(metric_name=metric_name, labels=labels, samples=samples)
            )
        return series_list
