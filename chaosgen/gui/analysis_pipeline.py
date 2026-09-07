"""
GUI-facing telemetry analysis pipeline (live stack or offline export).
"""

from __future__ import annotations

# --- START MODIFICATION ---
# P7: honor absolute windows via collect_range; pass AnomalySettings into detector
# --- END MODIFICATION ---

from dataclasses import dataclass, field
from typing import Any, Literal

from chaosgen.advisor.context_builder import ContextBuilder
from chaosgen.advisor.pipeline import run_advisor_pipeline
from chaosgen.config.settings import load_settings
from chaosgen.discovery import resolve_discovery_report
from chaosgen.ingestion.export_loader import ExportLoader
from chaosgen.ingestion.telemetry_factory import (
    build_telemetry_collector,
    check_live_stack,
)
import os
import pandas as pd
from datetime import datetime
from chaosgen.ingestion.window import resolve_collection_window
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.canonical_features import apply_canonical_features
from chaosgen.ml.cluster_labels import ClusterLabelStore
from chaosgen.schemas.scenarios import AdvisorReport, AnomalyCluster, AnomalySummary
from chaosgen.telemetry.guided_discovery import (
    discovered_from_dicts,
    discovered_to_pack_queries,
    resolve_scope_namespace,
)


@dataclass
class AnalysisRequest:
    source: Literal["live", "export"]
    prom_url: str
    loki_url: str
    lookback_hours: int = 24
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    export_path: str | None = None
    clustering_mode: str = "auto"
    n_clusters: int = 5
    generate_scenarios: bool = True
    llm_provider: str = "ollama"
    llm_model: str | None = None
    config_path: str | None = None
    skip_gatekeeper: bool = False
    model_path: str | None = None
    # --- START MODIFICATION ---
    # P0: Telemetry tab session override (not written to disk)
    telemetry_profile: str | None = None
    extra_packs: list[str] | None = None
    scope_namespace: str | None = None
    # Guided Custom: session ticks only (no ~/.config write)
    ingest_mode: Literal["default", "custom"] = "default"
    custom_queries: list[dict[str, Any]] | None = None
    # --- END MODIFICATION ---


@dataclass
class AnalysisResult:
    source_label: str
    metric_series: int
    total_samples: int
    log_streams: int
    feature_rows: int
    lookback_hours: float = 24.0
    clustering_label: str = ""
    window_start: datetime | None = None
    window_end: datetime | None = None
    clusters: list[AnomalyCluster] = field(default_factory=list)
    summaries: list[AnomalySummary] = field(default_factory=list)
    report: AdvisorReport = field(default_factory=lambda: AdvisorReport(anomalies_found=0))
    plot_path: str | None = None
    timeline_df: pd.DataFrame | None = None


def check_telemetry_endpoints(prom_url: str, loki_url: str) -> dict[str, str]:
    return check_live_stack(prom_url=prom_url, loki_url=loki_url)


def probe_guided_catalog_endpoints(
    prom_url: str,
    loki_url: str,
    namespace: str = "default",
) -> dict[str, Any]:
    """
    Background prefetch for Guided Custom Discovery (Ping OK → warm cache).

    Returns a plain dict so Qt signals stay pickle-friendly across threads:
    ``{"ok": True, "catalog": GuidedCatalog, "namespace": str}`` or
    ``{"ok": False, "error": str, "namespace": str}``.
    """
    from chaosgen.ingestion.telemetry_factory import (
        build_loki_client,
        build_prometheus_client,
    )
    from chaosgen.telemetry.guided_discovery import probe_guided_catalog

    ns = (namespace or "default").strip() or "default"
    try:
        prom = build_prometheus_client(prom_url)
        loki = build_loki_client(loki_url)
        catalog = probe_guided_catalog(prom, loki, namespace=ns)
        return {"ok": True, "catalog": catalog, "namespace": ns}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "namespace": ns}


def test_llm_provider(provider: str, model: str | None = None) -> tuple[bool, str]:
    """Verify LLM credentials and connectivity for the selected provider."""
    from chaosgen.config.secrets import MissingAPIKeyError, provider_credential_status

    ready, cred_msg = provider_credential_status(provider)
    if not ready:
        return False, cred_msg

    try:
        from chaosgen.advisor.llm_advisor import build_provider

        llm = build_provider(provider, model=model)
        ok, probe_msg = llm.probe()
        if ok:
            return True, probe_msg
        return False, probe_msg
    except MissingAPIKeyError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def run_gui_analysis_pipeline(
    request: AnalysisRequest,
    progress_cb=None,
) -> AnalysisResult:
    """Synchronous pipeline logic called by worker thread."""
    settings = load_settings(request.config_path)
    if request.clustering_mode:
        settings.anomaly.clustering_mode = request.clustering_mode
    if request.n_clusters:
        settings.anomaly.n_clusters = request.n_clusters

    # --- START MODIFICATION ---
    # P0: apply Telemetry-tab session ingest override (memory only)
    if request.telemetry_profile:
        settings.ingest.telemetry_profile = request.telemetry_profile
    if request.extra_packs is not None:
        settings.ingest.extra_packs = list(request.extra_packs)
    if request.scope_namespace:
        settings.ingest.scope_namespace = request.scope_namespace
    else:
        settings.ingest.scope_namespace = resolve_scope_namespace(settings)

    pack_override = None
    if request.ingest_mode == "custom":
        if not request.custom_queries:
            raise ValueError("Select at least one metric to proceed")
        pack_override = discovered_to_pack_queries(
            discovered_from_dicts(request.custom_queries)
        )
    # --- END MODIFICATION ---

    if request.source == "export" and request.export_path:
        if progress_cb:
            progress_cb(f"Loading telemetry export: {request.export_path}...")
        loader = ExportLoader.resolve_bundle(request.export_path)
        dataset = loader.load()
        source_label = f"export:{loader.export_root.name}"
        window = resolve_collection_window(dataset=dataset, settings=settings)
    else:
        window = resolve_collection_window(
            hours=request.lookback_hours,
            start=request.start_datetime,
            end=request.end_datetime,
            settings=settings,
        )
        if pack_override is not None:
            pack_note = (
                f"mode=custom queries={len(pack_override.queries)} "
                f"ns={settings.ingest.scope_namespace}"
            )
        else:
            pack_note = (
                f"packs={settings.ingest.telemetry_profile}"
                f"+{settings.ingest.extra_packs} ns={settings.ingest.scope_namespace}"
            )
        if progress_cb:
            progress_cb(
                f"Collecting live telemetry ({window.start.isoformat()} → "
                f"{window.end.isoformat()}, {window.lookback_hours:.1f}h; {pack_note})..."
            )
        collector = build_telemetry_collector(
            settings=settings,
            loki_url=request.loki_url,
            prom_url=request.prom_url,
            pack_queries=pack_override,
        )
        # MODIFIED: use collect_range so absolute GUI datetimes are honored
        dataset = collector.collect_range(
            start=window.start,
            end=window.end,
            step=settings.telemetry.step,
        )
        source_label = f"live ({window.lookback_hours:.1f}h)"

    fe = FeatureEngineer(settings=settings.features)
    features = fe.transform(dataset)
    features = apply_canonical_features(features, settings.features)

    clustering_label = (
        f"fixed-k={settings.anomaly.n_clusters}"
        if settings.anomaly.clustering_mode == "fixed"
        else "auto-k"
    )
    clusters: list[AnomalyCluster] = []
    summaries: list[AnomalySummary] = []
    plot_path = None
    feature_rows = 0
    timeline_df = None

    if not features.empty:
        # MODIFIED: pass AnomalySettings so GUI auto/fixed mode actually applies
        detector = AnomalyDetector(settings=settings.anomaly)
        model_path = request.model_path or settings.anomaly.default_model_path
        if model_path and os.path.exists(model_path):
            detector.load_model(model_path)
            label_store = ClusterLabelStore.sidecar_for_model(model_path)
            if label_store.path.exists():
                label_store.load()
        else:
            detector.fit(features)
            label_store = None

        clusters, summaries = detector.detect_and_summarize(features)
        if label_store is not None:
            summaries = label_store.annotate_summaries(summaries)
        feature_rows = len(features)
        if detector.last_chosen_k is not None:
            mode = settings.anomaly.clustering_mode
            clustering_label = f"{mode}-k={detector.last_chosen_k}"

        plot_path = os.path.join("scratch", "gui_anomaly_timeline.png")
        try:
            detector.plot_timeline(features, plot_path)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Failed to generate GUI timeline plot: %s", exc)
            plot_path = None

        try:
            timeline_df = detector.get_timeline_data(features)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Failed to compute GUI timeline data: %s", exc)
            timeline_df = None

    report = AdvisorReport(
        anomalies_found=len(clusters),
        clusters=clusters,
        summaries=summaries,
    )

    if request.generate_scenarios and summaries:
        pipeline_settings = load_settings(request.config_path)
        if request.llm_provider:
            pipeline_settings = pipeline_settings.model_copy(
                update={
                    "llm_provider": request.llm_provider,
                    "llm_model": request.llm_model or pipeline_settings.llm_model,
                }
            )
        discovery = resolve_discovery_report(settings=pipeline_settings)
        ctx = ContextBuilder(discovery).build()
        report = run_advisor_pipeline(
            clusters,
            summaries,
            settings=pipeline_settings,
            lookback_hours=float(window.lookback_hours),
            context=ctx,
            skip_gatekeeper=request.skip_gatekeeper,
            generate_chaos=True,
        )

    return AnalysisResult(
        source_label=source_label,
        metric_series=len(dataset.metrics),
        total_samples=dataset.total_samples,
        log_streams=len(dataset.logs),
        feature_rows=feature_rows,
        lookback_hours=float(window.lookback_hours),
        clustering_label=clustering_label,
        window_start=window.start,
        window_end=window.end,
        clusters=clusters,
        summaries=summaries,
        report=report,
        plot_path=plot_path,
        timeline_df=timeline_df,
    )


run_analysis = run_gui_analysis_pipeline
