"""Unit tests for Guided Custom discovery (filter / suggest / templates / pipeline)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from chaosgen.config.settings import ChaosGenSettings, IngestSettings
from chaosgen.gui.analysis_pipeline import AnalysisRequest, run_gui_analysis_pipeline
from chaosgen.schemas.telemetry import TelemetryDataset
from chaosgen.telemetry.guided_discovery import (
    DiscoveredQuery,
    build_catalog_from_names,
    classify_bucket,
    discovered_from_dicts,
    discovered_to_pack_queries,
    is_pack_aligned,
    keep_metric_name,
    probe_guided_catalog,
    resolve_scope_namespace,
    wrap_loki_log_error,
    wrap_promql,
)


def test_keep_metric_name_drops_noise_keeps_signal():
    assert keep_metric_name("http_requests_total")
    assert keep_metric_name("container_cpu_usage_seconds_total")
    assert not keep_metric_name("go_goroutines")
    assert not keep_metric_name("process_cpu_seconds_total")
    assert not keep_metric_name("promhttp_metric_handler_requests_total")


def test_classify_and_pack_aligned():
    assert classify_bucket("http_server_requests_seconds_count") == "traffic"
    assert classify_bucket("http_server_requests_seconds_bucket") == "latency"
    assert classify_bucket("istio_requests_total") in ("traffic", "errors")
    assert classify_bucket("container_memory_working_set_bytes") == "saturation"
    assert is_pack_aligned("http_requests_total", "traffic")
    assert is_pack_aligned("boutique:http_requests:rate5m", "traffic")
    assert is_pack_aligned("container_cpu_cfs_throttled_seconds_total", "saturation")
    # Lab regression: ops noise must NOT be Suggested
    assert not is_pack_aligned("alertmanager_notification_requests_total", "traffic")
    assert not is_pack_aligned("node_disk_flush_requests_total", "traffic")
    assert not is_pack_aligned("prometheus_http_requests_total", "traffic")


def test_suggested_bucket_diversity_excludes_ops_noise():
    """Lab root-cause: alphabetical traffic-only cap preferred alertmanager/node."""
    catalog = build_catalog_from_names(
        [
            "alertmanager_notification_requests_total",
            "node_disk_flush_requests_total",
            "prometheus_http_requests_total",
            "boutique:http_requests:rate5m",
            "boutique:http_errors:rate5m",
            "boutique:http_latency:p95",
            "http_request_duration_seconds_bucket",
            "container_cpu_usage_seconds_total",
            "container_memory_working_set_bytes",
            "rpc_server_duration_count",
            "go_goroutines",
        ],
        namespace="default",
        service_label="service_name",
        loki_apps=["frontend"],
        include_loki=True,
    )
    sug = catalog.suggested
    assert 1 <= len(sug) <= 6
    names = {q.metric for q in sug}
    assert "alertmanager_notification_requests_total" not in names
    assert "node_disk_flush_requests_total" not in names
    assert "prometheus_http_requests_total" not in names
    assert "boutique:http_requests:rate5m" in names
    buckets = {q.bucket for q in sug}
    # Diversity: not traffic-only when other buckets available
    assert "logs" in buckets or "saturation" in buckets or "errors" in buckets


def test_wrap_promql_uses_5m_window():
    q = wrap_promql("http_requests_total", "traffic", "service")
    assert "[5m]" in q
    assert "rate(" in q
    assert "by (service)" in q

    lat = wrap_promql("http_request_duration_seconds_bucket", "latency", "app")
    assert "histogram_quantile" in lat
    assert "[5m]" in lat


def test_wrap_loki_and_catalog_suggested():
    loki = wrap_loki_log_error("staging", ["frontend", "cart"])
    assert 'namespace="staging"' in loki
    assert "[5m]" in loki

    catalog = build_catalog_from_names(
        [
            "go_goroutines",
            "http_requests_total",
            "http_request_duration_seconds_bucket",
            "container_cpu_usage_seconds_total",
            "grpc_server_handled_total",
        ],
        namespace="default",
        service_label="service",
        loki_apps=["frontend"],
        include_loki=True,
    )
    assert catalog.queries
    assert any(q.suggested for q in catalog.queries)
    assert any(q.source == "loki" for q in catalog.queries)
    # noise dropped
    assert all(q.metric != "go_goroutines" for q in catalog.queries)


def test_discovered_to_pack_queries_and_empty_guard():
    rows = [
        DiscoveredQuery(
            id="http_requests_total",
            bucket="traffic",
            metric="http_requests_total",
            query='sum by (service) (rate(http_requests_total[5m]))',
            source="prometheus",
            suggested=True,
            service_label="service",
        )
    ]
    resolved = discovered_to_pack_queries(rows)
    assert resolved.pack_ids == ["guided"]
    assert resolved.queries[0].metric_name == "pack__http_requests_total"
    assert resolved.prometheus

    with pytest.raises(ValueError, match="at least one"):
        discovered_to_pack_queries([])


def test_discovered_from_dicts_roundtrip():
    raw = [
        {
            "id": "errors_total",
            "bucket": "errors",
            "metric": "http_errors_total",
            "query": "sum(rate(http_errors_total[5m]))",
            "source": "prometheus",
            "suggested": False,
            "service_label": "app",
            "display_name": "HTTP errors",
        }
    ]
    qs = discovered_from_dicts(raw)
    assert qs[0].id == "errors_total"
    assert qs[0].service_label == "app"
    pack = discovered_to_pack_queries(qs)
    assert pack.queries[0].signal == "errors_total"


def test_resolve_scope_namespace_fallback():
    assert resolve_scope_namespace(None) == "default"
    settings = ChaosGenSettings(ingest=IngestSettings(scope_namespace="demo"))
    assert resolve_scope_namespace(settings) == "demo"
    # Missing ingest ns falls through to kube / "default"
    bare = ChaosGenSettings()
    ns = resolve_scope_namespace(bare)
    assert isinstance(ns, str) and len(ns) > 0


def test_probe_guided_catalog_with_mocks():
    class FakeProm:
        def metric_names(self, max_names=5000):
            return (
                [
                    "http_requests_total",
                    "go_goroutines",
                    "container_memory_working_set_bytes",
                ],
                None,
            )

        def label_values(self, label):
            if label == "service":
                return ["frontend", "cart"], None
            if label in ("service_name", "app", "pod", "container_name"):
                return [], None
            return [], None

    class FakeLoki:
        def label_values(self, label="app"):
            return ["frontend", "adservice"], None

    cat = probe_guided_catalog(FakeProm(), FakeLoki(), namespace="default")
    assert "frontend" in cat.prom_services
    assert any(q.metric == "http_requests_total" for q in cat.queries)
    assert any(q.source == "loki" for q in cat.queries)


def test_pipeline_custom_mode_passes_pack_override(monkeypatch):
    captured: dict = {}

    def fake_build(settings=None, pack_queries=None, **kwargs):
        captured["pack_ids"] = (
            list(pack_queries.pack_ids) if pack_queries is not None else None
        )
        captured["n_queries"] = (
            len(pack_queries.queries) if pack_queries is not None else 0
        )
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
                extra_packs=["cadvisor"],
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
        ingest_mode="custom",
        custom_queries=[
            {
                "id": "http_requests_total",
                "bucket": "traffic",
                "metric": "http_requests_total",
                "query": "sum(rate(http_requests_total[5m]))",
                "source": "prometheus",
                "suggested": True,
                "service_label": "service",
            }
        ],
    )
    run_gui_analysis_pipeline(req)
    assert captured["pack_ids"] == ["guided"]
    assert captured["n_queries"] == 1
    assert captured["ns"] == "default"


def test_pipeline_custom_mode_rejects_empty(monkeypatch):
    monkeypatch.setattr(
        "chaosgen.gui.analysis_pipeline.load_settings",
        lambda _path=None: ChaosGenSettings(),
    )
    req = AnalysisRequest(
        source="live",
        prom_url="http://prom",
        loki_url="http://loki",
        lookback_hours=1,
        generate_scenarios=False,
        ingest_mode="custom",
        custom_queries=[],
    )
    with pytest.raises(ValueError, match="at least one"):
        run_gui_analysis_pipeline(req)


def test_factory_pack_queries_override():
    from chaosgen.ingestion.telemetry_factory import build_telemetry_collector
    from chaosgen.telemetry.pack_loader import PackQuery, ResolvedPackQueries

    override = ResolvedPackQueries(
        queries=[
            PackQuery(
                signal="custom_sig",
                source="prometheus",
                query="up",
                pack_id="guided",
                service_label="service",
                metric_name="pack__custom_sig",
            )
        ],
        pack_ids=["guided"],
    )
    settings = ChaosGenSettings(
        ingest=IngestSettings(telemetry_profile="boutique", extra_packs=["cadvisor"])
    )
    collector = build_telemetry_collector(settings=settings, pack_queries=override)
    assert collector.pack_queries is not None
    assert collector.pack_queries.pack_ids == ["guided"]
    assert collector.pack_queries.queries[0].signal == "custom_sig"


def test_probe_guided_catalog_endpoints_ok(monkeypatch):
    from chaosgen.gui.analysis_pipeline import probe_guided_catalog_endpoints
    from chaosgen.telemetry.guided_discovery import GuidedCatalog, DiscoveredQuery

    class FakeProm:
        pass

    class FakeLoki:
        pass

    monkeypatch.setattr(
        "chaosgen.ingestion.telemetry_factory.build_prometheus_client",
        lambda *a, **k: FakeProm(),
    )
    monkeypatch.setattr(
        "chaosgen.ingestion.telemetry_factory.build_loki_client",
        lambda *a, **k: FakeLoki(),
    )

    fake_cat = GuidedCatalog(
        queries=[
            DiscoveredQuery(
                id="http_requests_total",
                bucket="traffic",
                metric="http_requests_total",
                query="sum(rate(http_requests_total[5m]))",
                source="prometheus",
                suggested=True,
                service_label="service",
            )
        ],
        stack_summary="HTTP/RPC",
    )
    monkeypatch.setattr(
        "chaosgen.telemetry.guided_discovery.probe_guided_catalog",
        lambda *a, **k: fake_cat,
    )
    out = probe_guided_catalog_endpoints("http://p", "http://l", "demo")
    assert out["ok"] is True
    assert out["namespace"] == "demo"
    assert out["catalog"] is fake_cat
