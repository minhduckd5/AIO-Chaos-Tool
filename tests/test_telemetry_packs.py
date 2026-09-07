"""Telemetry pack loader + collector + IngestSettings pack fields."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from chaosgen.config.settings import ChaosGenSettings, IngestSettings
from chaosgen.ingestion.collector import TelemetryCollector
from chaosgen.ingestion.loki_client import LokiClient
from chaosgen.ingestion.telemetry_factory import build_telemetry_collector
from chaosgen.ml.canonical_features import extract_service_from_column
from chaosgen.schemas.telemetry import MetricSample, TimeSeries
from chaosgen.telemetry.pack_loader import (
    CANONICAL_SERVICE_KEY,
    list_available_packs,
    normalize_series_service_label,
    resolve_packs,
)


def test_list_available_packs_includes_shipped():
    packs = list_available_packs()
    assert "boutique" in packs
    assert "cadvisor" in packs
    assert "loki_system" in packs


def test_resolve_boutique_substitutes_namespace():
    resolved = resolve_packs("boutique", namespace="default")
    assert resolved.pack_ids == ["boutique"]
    assert resolved.loki == []
    by_signal = {q.signal: q for q in resolved.prometheus}
    assert "request_rate" in by_signal
    assert by_signal["request_rate"].metric_name == "pack__request_rate"
    replicas = by_signal["replicas_available"].query
    assert 'namespace="default"' in replicas
    assert "$namespace" not in replicas


def test_resolve_compose_extra_packs_and_override():
    resolved = resolve_packs(
        "boutique",
        extra_packs=["cadvisor", "loki_system"],
        namespace="chaos",
    )
    assert resolved.pack_ids == ["boutique", "cadvisor", "loki_system"]
    signals = {q.signal for q in resolved.queries}
    assert "cpu_usage" in signals
    assert "log_error_rate" in signals
    loki_q = next(q for q in resolved.loki if q.signal == "log_error_rate")
    assert 'namespace="chaos"' in loki_q.query
    assert loki_q.service_label == "app"


def test_resolve_unknown_pack_raises():
    with pytest.raises(ValueError, match="Unknown telemetry pack"):
        resolve_packs("not_a_real_pack")


def test_normalize_service_label_from_pack_key():
    labels = normalize_series_service_label(
        {"pod": "frontend-abc", "namespace": "default"},
        service_label="pod",
    )
    assert labels[CANONICAL_SERVICE_KEY] == "frontend-abc"
    assert labels["pod"] == "frontend-abc"


def test_normalize_service_label_fallback_aliases():
    labels = normalize_series_service_label(
        {"container_name": "cart", "namespace": "default"},
        service_label="service_name",
    )
    assert labels[CANONICAL_SERVICE_KEY] == "cart"


def test_ingest_settings_defaults_and_pack_validation():
    s = IngestSettings()
    assert s.telemetry_profile == "boutique"
    assert s.extra_packs == []
    assert s.scope_namespace == "default"
    assert s.allow_legacy_golden is True


def test_ingest_settings_rejects_unknown_pack():
    with pytest.raises(ValueError, match="Unknown telemetry pack"):
        IngestSettings(telemetry_profile="nope")


def test_ingest_settings_accepts_extra_packs():
    s = IngestSettings(
        telemetry_profile="boutique",
        extra_packs=["cadvisor", "loki_system"],
        scope_namespace="default",
    )
    assert s.extra_packs == ["cadvisor", "loki_system"]


def test_extract_service_from_pack_column():
    assert (
        extract_service_from_column("pack__error_rate__frontend__mean") == "frontend"
    )


def test_loki_parse_metric_matrix():
    payload = [
        {
            "metric": {"app": "cart"},
            "values": [[1_700_000_000.0, "0.5"], [1_700_000_060.0, "1.2"]],
        }
    ]
    series = LokiClient._parse_metric_results(payload, "log_error_rate")
    assert len(series) == 1
    assert series[0].labels["app"] == "cart"
    assert len(series[0].samples) == 2
    assert series[0].samples[0].value == 0.5


def test_collector_pack_path_skips_legacy_when_pack_returns_series():
    prom = MagicMock()
    loki = MagicMock()
    pack = resolve_packs("boutique", namespace="default")
    prom.query_range.return_value = [
        TimeSeries(
            metric_name="raw",
            labels={"service_name": "frontend"},
            samples=[
                MetricSample(timestamp=1_700_000_000.0, value=1.0, labels={}),
            ],
        )
    ]
    collector = TelemetryCollector(prometheus=prom, loki=loki)
    collector.pack_queries = pack
    collector.allow_legacy_golden = True
    collector.include_raw_logs = False

    start = datetime.fromtimestamp(1_700_000_000.0, tz=timezone.utc)
    end = datetime.fromtimestamp(1_700_000_600.0, tz=timezone.utc)
    ds = collector.collect_range(start, end, step="60s")

    assert ds.metrics
    assert all(m.metric_name.startswith("pack__") for m in ds.metrics)
    assert ds.metrics[0].labels.get("service") == "frontend"
    prom.query_golden_signals.assert_not_called()
    for call in prom.query_range.call_args_list:
        args, kwargs = call
        step = kwargs.get("step", args[3] if len(args) > 3 else None)
        assert step == "60s"


def test_collector_loki_pack_uses_same_step():
    prom = MagicMock()
    loki = MagicMock()
    prom.query_range.return_value = []
    loki.query_metric_range.return_value = [
        TimeSeries(
            metric_name="raw",
            labels={"app": "checkout"},
            samples=[MetricSample(timestamp=1.0, value=2.0, labels={})],
        )
    ]
    pack = resolve_packs(
        "boutique", extra_packs=["loki_system"], namespace="default"
    )
    collector = TelemetryCollector(prometheus=prom, loki=loki)
    collector.pack_queries = pack
    collector.allow_legacy_golden = False
    collector.include_raw_logs = False

    start = datetime.fromtimestamp(100.0, tz=timezone.utc)
    end = datetime.fromtimestamp(200.0, tz=timezone.utc)
    ds = collector.collect_range(start, end, step="15s")

    assert any(m.metric_name == "pack__log_error_rate" for m in ds.metrics)
    for call in loki.query_metric_range.call_args_list:
        args, kwargs = call
        step = kwargs.get("step", args[3] if len(args) > 3 else None)
        assert step == "15s"
        start_arg = kwargs.get("start", args[1] if len(args) > 1 else None)
        assert start_arg == start.timestamp()


def test_factory_attaches_packs_from_settings():
    settings = ChaosGenSettings(
        ingest=IngestSettings(
            telemetry_profile="boutique",
            extra_packs=["cadvisor"],
            scope_namespace="default",
        )
    )
    collector = build_telemetry_collector(settings=settings)
    assert collector.pack_queries is not None
    assert "cadvisor" in collector.pack_queries.pack_ids


def test_format_pack_status_line():
    from chaosgen.telemetry.pack_loader import format_pack_status_line

    line = format_pack_status_line(
        "boutique", ["cadvisor", "loki_system"], "staging"
    )
    assert "boutique" in line
    assert "ns: staging" in line
    assert "queries mapped" in line


def test_pack_combo_roundtrip():
    from chaosgen.telemetry.pack_loader import (
        ingest_to_pack_combo_key,
        pack_combo_key_to_ingest,
    )

    key = ingest_to_pack_combo_key("boutique", ["cadvisor", "loki_system"])
    assert key == "boutique+cadvisor+loki"
    profile, extras = pack_combo_key_to_ingest(key)
    assert profile == "boutique"
    assert extras == ["cadvisor", "loki_system"]


def test_analysis_request_session_override_applied(monkeypatch):
    """Pipeline honors Telemetry-tab ingest fields without disk save."""
    from datetime import datetime, timezone

    from chaosgen.gui.analysis_pipeline import AnalysisRequest, run_gui_analysis_pipeline
    from chaosgen.schemas.telemetry import TelemetryDataset

    captured: dict = {}

    def fake_build(settings=None, **kwargs):
        captured["profile"] = settings.ingest.telemetry_profile
        captured["extras"] = list(settings.ingest.extra_packs)
        captured["ns"] = settings.ingest.scope_namespace

        class _C:
            def collect_range(self, **_kw):
                now = datetime.now(tz=timezone.utc)
                return TelemetryDataset(
                    metrics=[], logs=[], collection_start=now, collection_end=now
                )

        return _C()

    monkeypatch.setattr(
        "chaosgen.gui.analysis_pipeline.build_telemetry_collector", fake_build
    )
    monkeypatch.setattr(
        "chaosgen.gui.analysis_pipeline.load_settings",
        lambda _path=None: ChaosGenSettings(
            ingest=IngestSettings(
                telemetry_profile="boutique",
                extra_packs=[],
                scope_namespace="default",
            )
        ),
    )

    req = AnalysisRequest(
        source="live",
        prom_url="http://prom",
        loki_url="http://loki",
        lookback_hours=1,
        generate_scenarios=False,
        telemetry_profile="boutique",
        extra_packs=["cadvisor", "loki_system"],
        scope_namespace="staging",
    )
    run_gui_analysis_pipeline(req)
    assert captured["profile"] == "boutique"
    assert captured["extras"] == ["cadvisor", "loki_system"]
    assert captured["ns"] == "staging"
