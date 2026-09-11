import logging
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)


class SteadyStateValidator:
    """
    Validates the steady state of the system using defined hypotheses.
    Supports HTTP checks and basic Prometheus queries.
    """

    def validate(self, hypothesis: Dict[str, Any]) -> bool:
        """
        Validate a set of hypotheses.

        Args:
            hypothesis: Dictionary defining checks (e.g. {'http_health': 'http://localhost:8080/health', 'prometheus_query': '...'})

        Returns:
            bool: True if all checks pass, False otherwise.
        """
        if not hypothesis:
            return True

        results = []

        if "http_health" in hypothesis:
            results.append(self._check_http(hypothesis["http_health"]))

        if "prometheus" in hypothesis:
            prom_config = hypothesis["prometheus"]
            results.append(self._check_prometheus(prom_config))

        return all(results)

    def _check_http(self, url: str, expected_code: int = 200, timeout: int = 5) -> bool:
        try:
            response = requests.get(url, timeout=timeout)
            return response.status_code == expected_code
        except Exception as e:
            print(f"Health check failed for {url}: {e}")
            return False

    def _check_prometheus(self, config: Dict[str, Any]) -> bool:
        """
        Check a Prometheus query.
        Expected config:
        {
            'url': 'http://prometheus:9090',
            'query': 'up{job="my-service"}',
            'condition': '== 1'
        }
        """
        url = config.get("url")
        query = config.get("query")
        # Simplified logic: just check if query returns any result or specific value
        # In a real impl, we'd parse the condition.

        if not url or not query:
            return False

        # --- START MODIFICATION ---
        # Wire-level observability for Approve SS: log the exact Prom base URL
        # before HTTP so banner / _cg_settings mismatches can be proven, not inferred.
        endpoint = f"{str(url).rstrip('/')}/api/v1/query"
        logger.info(
            "Steady-state Prometheus check: url=%s query=%s",
            url,
            query,
        )
        # --- END MODIFICATION ---
        try:
            response = requests.get(endpoint, params={"query": query})
            data = response.json()
            if data["status"] == "success":
                # Basic check: did we get any result?
                results = data["data"]["result"]
                return len(results) > 0
            return False
        except Exception as e:
            print(f"Prometheus check failed: {e}")
            return False
