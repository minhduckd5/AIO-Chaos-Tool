"""Tests for UCAL SteadyStateValidator (P6 coverage)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from chaosgen.ucal.validation import SteadyStateValidator


class TestSteadyStateValidator:
    def test_empty_hypothesis_passes(self):
        assert SteadyStateValidator().validate({}) is True
        assert SteadyStateValidator().validate(None) is True

    def test_http_health_ok(self):
        mock_resp = MagicMock(status_code=200)
        with patch("chaosgen.ucal.validation.requests.get", return_value=mock_resp):
            ok = SteadyStateValidator().validate({"http_health": "http://localhost/health"})
        assert ok is True

    def test_http_health_failure(self):
        with patch("chaosgen.ucal.validation.requests.get", side_effect=ConnectionError("down")):
            ok = SteadyStateValidator().validate({"http_health": "http://localhost/health"})
        assert ok is False

    def test_prometheus_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "success",
            "data": {"result": [{"metric": {}}]},
        }
        with patch("chaosgen.ucal.validation.requests.get", return_value=mock_resp):
            ok = SteadyStateValidator().validate(
                {
                    "prometheus": {
                        "url": "http://prometheus:9090",
                        "query": 'up{job="api"}',
                    }
                }
            )
        assert ok is True

    def test_prometheus_logs_url_before_http(self, caplog):
        # MODIFIED: wire-level URL must be visible in OUTPUT for Approve diagnosis
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "success",
            "data": {"result": [{"metric": {}}]},
        }
        with (
            caplog.at_level("INFO", logger="chaosgen.ucal.validation"),
            patch("chaosgen.ucal.validation.requests.get", return_value=mock_resp) as get,
        ):
            ok = SteadyStateValidator().validate(
                {
                    "prometheus": {
                        "url": "http://10.50.1.220:9090",
                        "query": 'up{job=~".+"}',
                    }
                }
            )
        assert ok is True
        get.assert_called_once()
        assert get.call_args.args[0] == "http://10.50.1.220:9090/api/v1/query"
        assert any(
            "Steady-state Prometheus check: url=http://10.50.1.220:9090" in r.message
            for r in caplog.records
        )

    def test_prometheus_missing_config(self):
        assert SteadyStateValidator().validate({"prometheus": {"url": "http://x"}}) is False

    def test_combined_checks_all_must_pass(self):
        mock_resp = MagicMock(status_code=500)
        with patch("chaosgen.ucal.validation.requests.get", return_value=mock_resp):
            ok = SteadyStateValidator().validate(
                {
                    "http_health": "http://localhost/health",
                    "prometheus": {"url": "http://p:9090", "query": "up"},
                }
            )
        assert ok is False
