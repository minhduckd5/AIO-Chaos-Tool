"""Factory helpers for live telemetry collectors."""

from __future__ import annotations

import logging

from chaosgen.config.secrets import load_secrets
from chaosgen.config.settings import ChaosGenSettings
from chaosgen.config.telemetry_endpoints import resolve_loki_url, resolve_prometheus_url
from chaosgen.ingestion.collector import TelemetryCollector, DEFAULT_LOG_QUERY
from chaosgen.ingestion.loki_client import LokiClient
from chaosgen.ingestion.prometheus_client import PrometheusClient
from chaosgen.telemetry.pack_loader import ResolvedPackQueries

logger = logging.getLogger(__name__)


def build_prometheus_client(
    url: str | None = None,
    settings: ChaosGenSettings | None = None,
) -> PrometheusClient:
    secrets = load_secrets()
    return PrometheusClient(
        url or resolve_prometheus_url(settings),
        bearer_token=secrets.get("PROMETHEUS_TOKEN"),
    )


def build_loki_client(
    url: str | None = None,
    settings: ChaosGenSettings | None = None,
) -> LokiClient:
    secrets = load_secrets()
    return LokiClient(
        url or resolve_loki_url(settings),
        bearer_token=secrets.get("LOKI_TOKEN"),
    )


def build_telemetry_collector(
    settings: ChaosGenSettings | None = None,
    log_query: str | None = None,
    prom_url: str | None = None,
    loki_url: str | None = None,
    pack_queries: ResolvedPackQueries | None = None,
) -> TelemetryCollector:
    # MODIFIED: P8 — log_query / custom_promql from settings.ingest
    # MODIFIED: form-first telemetry packs attached when settings present
    # MODIFIED: pack_queries override for Guided Custom (session-only; no disk write)
    resolved_log_query = log_query
    if resolved_log_query is None:
        if settings is not None:
            resolved_log_query = settings.ingest.log_query
        else:
            resolved_log_query = DEFAULT_LOG_QUERY
    prom = build_prometheus_client(prom_url, settings)
    loki = build_loki_client(loki_url, settings)
    collector = TelemetryCollector(
        prometheus=prom, loki=loki, log_query=resolved_log_query
    )
    if settings is not None:
        if settings.ingest.custom_promql:
            collector.default_custom_queries = dict(settings.ingest.custom_promql)
        collector.allow_legacy_golden = bool(settings.ingest.allow_legacy_golden)
    # --- START MODIFICATION ---
    # Session override (Guided Custom) wins over settings packs
    if pack_queries is not None:
        collector.pack_queries = pack_queries
    elif settings is not None:
        try:
            from chaosgen.telemetry.pack_loader import resolve_packs

            collector.pack_queries = resolve_packs(
                settings.ingest.telemetry_profile,
                extra_packs=settings.ingest.extra_packs,
                namespace=settings.ingest.scope_namespace,
            )
        except ValueError as exc:
            logger.warning("Telemetry pack resolve failed (%s); legacy golden only", exc)
            collector.pack_queries = None
    # --- END MODIFICATION ---
    return collector


def check_live_stack(
    settings: ChaosGenSettings | None = None,
    prom_url: str | None = None,
    loki_url: str | None = None,
) -> dict[str, str]:
    """Health-check Prometheus and Loki with auth-aware probes."""
    prom = build_prometheus_client(prom_url, settings)
    loki = build_loki_client(loki_url, settings)
    prom_ok, prom_msg = prom.probe()
    loki_ok, loki_msg = loki.probe()
    return {
        "prometheus": prom_msg if prom_ok else f"FAIL: {prom_msg}",
        "loki": loki_msg if loki_ok else f"FAIL: {loki_msg}",
    }
