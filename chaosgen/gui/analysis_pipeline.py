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
from chaosgen.schemas.scenarios import AdvisorReport, AnomalyCluster, AnomalySummary


@dataclass
class AnalysisRequest:
    source: Literal["live", "export"]
    prom_url: str
    loki_url: str
    lookback_hours: int = 24
    export_path: str | None = None
    generate_scenarios: bool = True
    llm_provider: str = "ollama"
    llm_model: str | None = None
    config_path: str | None = None
    skip_gatekeeper: bool = False


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


def run_analysis(request: AnalysisRequest) -> AnalysisResult:
    if request.source == "export":
        if not request.export_path:
            raise ValueError("Export path is required for offline analysis.")
        loader = ExportLoader.resolve_bundle(request.export_path)
        dataset = loader.load()
        source_label = f"export:{loader.export_root.name}"
    else:
        collector = build_telemetry_collector(
            prom_url=request.prom_url,
            loki_url=request.loki_url,
        )
        dataset = collector.collect_baseline(duration_hours=request.lookback_hours)
        source_label = "live"

    clusters, summaries, feature_rows = analyze_dataset(dataset)

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
    )
