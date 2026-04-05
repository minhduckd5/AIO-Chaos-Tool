import requests
from typing import Dict, Any, Optional
import time

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
        
        if 'http_health' in hypothesis:
            results.append(self._check_http(hypothesis['http_health']))
            
        if 'prometheus' in hypothesis:
            prom_config = hypothesis['prometheus']
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
        url = config.get('url')
        query = config.get('query')
        # Simplified logic: just check if query returns any result or specific value
        # In a real impl, we'd parse the condition.
        
        if not url or not query:
            return False

        try:
            response = requests.get(f"{url}/api/v1/query", params={'query': query})
            data = response.json()
            if data['status'] == 'success':
                # Basic check: did we get any result?
                results = data['data']['result']
                return len(results) > 0
            return False
        except Exception as e:
            print(f"Prometheus check failed: {e}")
            return False





