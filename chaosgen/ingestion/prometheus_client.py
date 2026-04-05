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


class PrometheusClient:
    """Wraps the Prometheus HTTP API for metric ingestion."""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def query(self, promql: str) -> List[MetricSample]:
        """Execute an instant PromQL query."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/query",
                params={"query": promql},
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

    def health_check(self) -> bool:
        """Verify Prometheus is reachable."""
        try:
            resp = requests.get(
                f"{self.base_url}/-/healthy", timeout=5
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

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
