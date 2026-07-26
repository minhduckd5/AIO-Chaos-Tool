"""
GUI-facing telemetry analysis pipeline (live stack or offline export).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from chaosgen.advisor.context_builder import ContextBuilder
from chaosgen.advisor.pipeline import run_advisor_pipeline
from chaosgen.config.settings import load_settings
from chaosgen.discovery import resolve_discovery_report
from chaosgen.ingestion.analysis import analyze_dataset
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
from chaosgen.schemas.scenarios import AdvisorReport, AnomalyCluster, AnomalySummary


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


@dataclass
class AnalysisResult:
    source_label: str
    metric_series: int
    total_samples: int
    log_streams: int
    feature_rows: int
    clusters: list[AnomalyCluster] = field(default_factory=list)
    summaries: list[AnomalySummary] = field(default_factory=list)
    report: AdvisorReport = field(default_factory=lambda: AdvisorReport(anomalies_found=0))
    plot_path: str | None = None
    timeline_df: pd.DataFrame | None = None


def check_telemetry_endpoints(prom_url: str, loki_url: str) -> dict[str, str]:
    return check_live_stack(prom_url=prom_url, loki_url=loki_url)


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
        source_label = f"Live ({window.lookback_hours:.1f}h window)"
        if progress_cb:
            progress_cb(f"Collecting live telemetry from Prometheus / Loki ({window.lookback_hours:.1f}h window)...")
        collector = build_telemetry_collector(
            loki_url=request.loki_url,
        )
        dataset = collector.collect_baseline(duration_hours=request.lookback_hours)
        source_label = "live"

    fe = FeatureEngineer(settings=settings.features)
    features = fe.transform(dataset)
    
    if features.empty:
        clusters, summaries = [], []
        plot_path = None
        feature_rows = 0
        timeline_df = None
    else:
        detector = AnomalyDetector()
        if request.model_path and os.path.exists(request.model_path):
            detector.load_model(request.model_path)
        else:
            detector.fit(features)
        
        clusters, summaries = detector.detect_and_summarize(features)
        feature_rows = len(features)
        
        # Generate plot
        plot_path = os.path.join("scratch", "gui_anomaly_timeline.png")
        try:
            detector.plot_timeline(features, plot_path)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Failed to generate GUI timeline plot: {exc}")
            plot_path = None

        # Compute timeline data for interactive GUI plot
        try:
            timeline_df = detector.get_timeline_data(features)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Failed to compute GUI timeline data: {exc}")
            timeline_df = None

    report = AdvisorReport(
        anomalies_found=len(clusters),
        clusters=clusters,
        summaries=summaries,
    )

    if request.generate_scenarios and summaries:
        settings = load_settings(request.config_path)
        if request.llm_provider:
            settings = settings.model_copy(
                update={
                    "llm_provider": request.llm_provider,
                    "llm_model": request.llm_model or settings.llm_model,
                }
            )
        discovery = resolve_discovery_report(settings=settings)
        ctx = ContextBuilder(discovery).build()
        report = run_advisor_pipeline(
            clusters,
            summaries,
            settings=settings,
            lookback_hours=float(request.lookback_hours),
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
        clusters=clusters,
        summaries=summaries,
        report=report,
        plot_path=plot_path,
        timeline_df=timeline_df,
    )


run_analysis = run_gui_analysis_pipeline
