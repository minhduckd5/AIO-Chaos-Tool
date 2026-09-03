"""
ChaosGen CLI — AI-Driven Chaos Scenario Generator

Command groups:
    chaosgen discover           [scoped off] Scan environment, architecture, observability
    chaosgen analyze            Analyze live or exported telemetry (anomaly detection)
    chaosgen bootstrap          Install missing observability tooling
    chaosgen generate           AI chaos scenario generation
    chaosgen incidents          List described/unknown incidents from a saved report
    chaosgen promote            Promote a described incident to the dynamic catalog
    chaosgen run                Execute approved scenarios (HITL gate)
    chaosgen evaluate           KPI and A/B evaluation reports
    chaosgen verdict            Operational expectation PASS/FAIL + rationale (P0-B)
    chaosgen status             Module and orchestrator status
    chaosgen config             Settings & API key management
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version="0.3.0", prog_name="ChaosGen")
def main() -> None:
    """ChaosGen — AI-Driven Chaos Scenario Generator."""


# ---------------------------------------------------------------------------
# discover  (config-file-first: reads settings.yaml, minimal flags)
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--config", "config_path", default=None,
    help="Path to a settings.yaml file (default: auto-resolved XDG path).",
)
@click.option(
    "--arch", default=None,
    type=click.Choice(["microservices", "monolith", "modular_monolith", "event_driven", "client_server", "serverless"]),
    help="Override architecture hint (overrides settings.yaml for this run).",
)
@click.option(
    "--env", "env_type", default=None,
    type=click.Choice(["kubernetes", "docker_compose", "bare_metal", "cloud_vm", "serverless"]),
    help="Override environment hint.",
)
@click.option(
    "--output", type=click.Choice(["table", "json"]),
    default="table", show_default=True, help="Output format.",
)
def discover(config_path, arch, env_type, output):
    """Probe environment, classify architecture, detect observability tools."""
    from chaosgen.config.scope import DISCOVERY_ENABLED, scope_notice
    from chaosgen.config.settings import load_settings
    from chaosgen.discovery import resolve_discovery_report, run_full_discovery
    from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType, ProbeOutcome

    if not DISCOVERY_ENABLED:
        click.secho(
            f"[ChaosGen] Discovery is temporarily disabled.\n  {scope_notice()}",
            fg="yellow",
        )
        click.echo("  Pipeline is focused on microservices. Use `chaosgen generate` to continue.")
        sys.exit(0)

    settings = load_settings(config_path)

    if arch:
        settings.hints.architecture = ArchitectureType(arch)
    if env_type:
        settings.hints.environment = EnvironmentType(env_type)

    click.echo("[ChaosGen] Running hybrid discovery scan...")
    try:
        report = run_full_discovery(settings=settings)
    except Exception as exc:
        click.secho(f"Discovery failed: {exc}", fg="red")
        sys.exit(1)

    if output == "json":
        click.echo(report.model_dump_json(indent=2))
        return

    # Table output
    click.secho("\n=== Environment ===", bold=True)
    env = report.environment
    click.echo(f"  Type:        {env.type.value}")
    click.echo(f"  Cloud:       {env.cloud_provider or '-'}")
    click.echo(f"  Nodes:       {env.node_count or '-'}")
    click.echo(f"  Service Mesh:{' yes' if env.has_service_mesh else ' no'}")

    click.secho("\n=== Architecture ===", bold=True)
    a = report.architecture
    click.echo(f"  Type:        {a.type.value}")
    click.echo(f"  Services:    {a.service_count}")
    click.echo(f"  Broker:      {'yes' if a.has_message_broker else 'no'}")
    click.echo(f"  Confidence:  {a.confidence:.0%}")
    for signal in a.signals:
        click.echo(f"    - {signal}")

    click.secho("\n=== Observability ===", bold=True)
    obs = report.observability
    for tool_name, has, endpoint in [
        ("Metrics", obs.has_metrics, obs.metrics_endpoint),
        ("Logs", obs.has_logs, obs.logs_endpoint),
        ("Traces", obs.has_traces, obs.traces_endpoint),
    ]:
        mark = "OK" if has else "MISSING"
        color = "green" if has else "red"
        click.secho(f"  {tool_name}: {mark}  ({endpoint or '-'})", fg=color)

    auth_issues = [
        s for s in report.signals if s.probe_outcome == ProbeOutcome.AUTH_REJECTED
    ]
    if auth_issues:
        click.secho("\n  Auth issues (tool exists but credentials rejected):", fg="yellow")
        for s in auth_issues:
            click.echo(f"    - {s.key}: {s.message}")

    if obs.missing:
        missing = ", ".join(t.value for t in obs.missing)
        click.secho(f"\n  Missing: {missing}", fg="yellow")
        click.echo("  Run `chaosgen bootstrap` to install missing tools.")

    mismatch = [s for s in report.signals if s.flag == "USER_HEURISTIC_MISMATCH"]
    if mismatch:
        click.secho("\n=== Warnings ===", fg="yellow", bold=True)
        for s in mismatch:
            click.echo(f"  {s.message}")

    click.secho("\n=== Service Map ===", bold=True)
    smap = report.service_map
    click.echo(f"  Nodes: {len(smap.nodes)}  Edges: {len(smap.edges)}")
    for node in smap.nodes[:10]:
        click.echo(f"    [{node.node_type}] {node.name}")

    if report.discovery_errors:
        click.secho("\n  Non-fatal errors:", fg="yellow")
        for err in report.discovery_errors:
            click.echo(f"  {err}")


# ---------------------------------------------------------------------------
# analyze  (live registry-vm stack OR offline export bundle)
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--live", is_flag=True, default=False,
    help="Pull telemetry from live Prometheus/Loki (default when neither --live nor --export).",
)
@click.option(
    "--export", "export_path", default=None, type=click.Path(exists=True),
    help="Path to an export bundle, CSV, zip/tar, or parent exports/ directory.",
)
@click.option(
    "--check", is_flag=True, default=False,
    help="Only verify live Prometheus/Loki connectivity (Way 1).",
)
@click.option("--hours", default=None, type=int, help="Lookback hours for --live (default: from settings or 24).")
@click.option(
    "--start", type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]), default=None,
    help="Absolute start datetime (UTC) for live collection.",
)
@click.option(
    "--end", type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]), default=None,
    help="Absolute end datetime (UTC) for live collection.",
)
@click.option(
    "--generate", "with_generate", is_flag=True, default=False,
    help="After analysis, run scenario generation (like chaosgen generate).",
)
@click.option("--provider", type=click.Choice(["ollama", "openai", "anthropic", "groq"]), default=None)
@click.option("--top-n", default=None, type=int, help="Ranked scenario cap (default: settings.advisor.top_n_scenarios).")
@click.option(
    "--confidence-threshold",
    default=None,
    type=float,
    help="Drop LLM hypotheses below this confidence (default: settings.advisor.confidence_threshold).",
)
@click.option("--config", "config_path", default=None, help="Path to settings.yaml.")
@click.option("--model-path", default=None, help="Path to pre-trained ML model joblib file.")
def analyze(
    live, export_path, check, hours, start, end, with_generate, provider, top_n,
    confidence_threshold, config_path, model_path,
):
    """Analyze metrics/logs from live stack or offline export; optional scenario generation."""
    from chaosgen.config.settings import load_settings
    from chaosgen.config.telemetry_endpoints import resolve_loki_url, resolve_prometheus_url
    from chaosgen.ingestion.analysis import analyze_dataset
    from chaosgen.ingestion.export_loader import ExportLoader
    from chaosgen.ingestion.telemetry_factory import build_telemetry_collector, check_live_stack
    from chaosgen.ingestion.window import resolve_collection_window

    settings = load_settings(config_path)

    if check:
        prom_url = resolve_prometheus_url(settings)
        loki_url = resolve_loki_url(settings)
        click.echo("[ChaosGen] Live stack endpoints:")
        click.echo(f"  Prometheus: {prom_url}")
        click.echo(f"  Loki:       {loki_url}")
        health = check_live_stack(settings)
        for name, msg in health.items():
            ok = not str(msg).startswith("FAIL")
            click.secho(f"  {name}: {msg}", fg="green" if ok else "red")
        if any(str(msg).startswith("FAIL") for msg in health.values()):
            sys.exit(1)
        return

    # MODIFIED: P7 — reject --hours together with absolute --start/--end
    if hours is not None and (start is not None or end is not None):
        raise click.ClickException(
            "Use either --hours (relative) or --start/--end (absolute), not both."
        )
    if (start is None) ^ (end is None):
        raise click.ClickException("Both --start and --end are required for absolute windows.")

    if export_path:
        click.echo(f"[ChaosGen] Loading offline export: {export_path}")
        loader = ExportLoader.resolve_bundle(export_path)
        dataset = loader.load()
        source = f"export:{loader.export_root.name}"
        window = resolve_collection_window(dataset=dataset, settings=settings)
    else:
        window = resolve_collection_window(hours=hours, start=start, end=end, settings=settings)
        click.echo(f"[ChaosGen] Collecting live telemetry ({window.start.isoformat()} -> {window.end.isoformat()}, {window.lookback_hours:.1f}h window)...")
        collector = build_telemetry_collector(settings)
        if start and end:
            dataset = collector.collect_range(start=window.start, end=window.end, step=settings.telemetry.step)
        else:
            dataset = collector.collect_baseline(duration_hours=int(round(window.lookback_hours)), step=settings.telemetry.step)
        source = "live"

    click.echo(
        f"  Series: {len(dataset.metrics)} | "
        f"Samples: {dataset.total_samples} | "
        f"Log streams: {len(dataset.logs)}"
    )

    clusters, summaries, feature_rows = analyze_dataset(dataset, model_path=model_path, settings=settings)
    click.secho(f"\n=== Anomaly Analysis ({source}) ===", bold=True)
    click.echo(f"  Feature windows: {feature_rows}")
    click.echo(f"  Anomaly clusters: {len(clusters)}")
    click.echo(f"  Lookback hours: {window.lookback_hours:.2f}")

    if not summaries:
        click.secho("  No anomalies detected (or insufficient feature data).", fg="yellow")
        if not with_generate:
            return
    else:
        for i, s in enumerate(summaries[:10], 1):
            feats = ", ".join(f"{n}={v:.2f}" for n, v in s.top_features[:3])
            click.echo(f"  [{i}] {s.service_name} severity={s.severity:.2f} — {feats}")

    if not with_generate:
        click.echo("\nRun with `--generate` to feed anomalies into the advisor pipeline.")
        return

    _generate_from_analysis(
        settings=settings,
        clusters=clusters,
        summaries=summaries,
        lookback_hours=window.lookback_hours,
        provider=provider,
        model=None,
        top_n=top_n if top_n is not None else settings.advisor.top_n_scenarios,
        confidence_threshold=confidence_threshold,
        output="table",
        config_path=config_path,
    )


# ---------------------------------------------------------------------------
# train-model  (offline baseline model training step)
# ---------------------------------------------------------------------------


@main.command("train-model")
@click.option(
    "--export", "export_paths", default=None, multiple=True, type=click.Path(exists=True),
    help="Prom/Loki bundle dir, CSV file/dir, or zip/tar of the same. Repeatable.",
)
@click.option(
    "--output-model", "output_path", default="./default_model.joblib",
    help="Output file path for the trained model .joblib.",
)
@click.option(
    "--live", is_flag=True, default=False,
    help="Train on live Prometheus/Loki telemetry (backup/alternative).",
)
@click.option("--hours", default=24, show_default=True, help="Lookback hours when using --live.")
@click.option("--config", "config_path", default=None, help="Path to settings.yaml.")
def train_model(export_paths, output_path, live, hours, config_path):
    """Fit IsolationForest & KMeans on reference dataset(s) and serialize the model."""
    import pandas as pd
    from chaosgen.config.settings import load_settings
    from chaosgen.ingestion.export_loader import ExportLoader
    from chaosgen.ingestion.telemetry_factory import build_telemetry_collector
    from chaosgen.ml.anomaly_detector import AnomalyDetector
    from chaosgen.ml.canonical_features import (
        CANONICAL_SCHEMA_VERSION,
        apply_canonical_features,
    )
    from chaosgen.ml.cluster_labels import ClusterLabelStore
    from chaosgen.ml.feature_engineering import FeatureEngineer

    settings = load_settings(config_path)

    if not export_paths and not live:
        raise click.ClickException("Must specify at least one --export (reference dataset) or --live.")

    feature_dfs = []
    fe = FeatureEngineer(settings=settings.features)

    if export_paths:
        for path in export_paths:
            click.echo(f"[ChaosGen] Loading offline training export: {path}")
            loader = ExportLoader.resolve_bundle(path)
            dataset = loader.load()
            click.echo(
                f"  Series: {len(dataset.metrics)} | "
                f"Samples: {dataset.total_samples} | "
                f"Log streams: {len(dataset.logs)}"
            )
            features = fe.transform(dataset)
            features = apply_canonical_features(features, settings.features)
            if not features.empty:
                feature_dfs.append(features)
    else:
        click.echo(f"[ChaosGen] Collecting live telemetry for training ({hours}h lookback)...")
        collector = build_telemetry_collector(settings)
        dataset = collector.collect_baseline(duration_hours=hours, step=settings.telemetry.step)
        click.echo(
            f"  Series: {len(dataset.metrics)} | "
            f"Samples: {dataset.total_samples} | "
            f"Log streams: {len(dataset.logs)}"
        )
        features = fe.transform(dataset)
        features = apply_canonical_features(features, settings.features)
        if not features.empty:
            feature_dfs.append(features)

    if not feature_dfs:
        raise click.ClickException("No feature data extracted from dataset(s); aborting training.")

    # MODIFIED: align columns across exports; missing metrics → 0 (avoids NaN KMeans crash)
    combined_features = pd.concat(feature_dfs, axis=0, sort=True)
    combined_features = combined_features.fillna(0.0)
    combined_features = combined_features[~combined_features.index.duplicated(keep="first")]
    combined_features = combined_features.sort_index()

    click.echo(f"[ChaosGen] Training Anomaly Detector (IsolationForest & KMeans) on {len(combined_features)} samples...")
    detector = AnomalyDetector(settings=settings.anomaly)
    if settings.features.canonical_enabled:
        detector.canonical_schema_version = CANONICAL_SCHEMA_VERSION
    detector.fit(combined_features)

    clusters = []
    try:
        detected = detector.detect(combined_features)
        if isinstance(detected, list):
            clusters = detected
    except Exception as e:
        click.secho(f"Warning during initial KMeans fit: {e}", fg="yellow")

    click.echo(f"[ChaosGen] Saving model weights to: {output_path}")
    detector.save_model(output_path)

    # MODIFIED: P0-A — write editable cluster label sidecar stub
    cluster_ids = [c.cluster_id for c in clusters]
    if not cluster_ids and getattr(detector, "last_chosen_k", None):
        cluster_ids = list(range(int(detector.last_chosen_k)))
    if cluster_ids:
        store = ClusterLabelStore.sidecar_for_model(output_path)
        label_path = store.write_stub(cluster_ids, model_path=output_path)
        click.echo(f"[ChaosGen] Cluster label stub written to: {label_path}")
        click.echo("  Edit name/description fields, then reuse via --model-path / anomaly.default_model_path.")

    click.secho("Model training and serialization completed successfully.", fg="green")


# ---------------------------------------------------------------------------
# retrain-anomaly  (Phase 2 — baseline + capped CTK corpus)
# ---------------------------------------------------------------------------


@main.command("retrain-anomaly")
@click.option(
    "--export", "export_paths", required=True, multiple=True, type=click.Path(exists=True),
    help="Healthy baseline export bundle(s). Repeatable.",
)
@click.option(
    "--include-ctk-runs", "ctk_runs_dir", default=None, type=click.Path(exists=True),
    help="Optional cached per-run telemetry dir (scratch/telemetry/runs).",
)
@click.option(
    "--output-model", "output_path", default="./default_model.joblib",
    help="Output path for retrained .joblib.",
)
@click.option(
    "--contamination-mode",
    type=click.Choice(["baseline_only", "proportional", "fixed"], case_sensitive=False),
    default="proportional",
    show_default=True,
    help="How to set IF contamination when merging CTK windows.",
)
@click.option(
    "--contamination", "fixed_contamination", default=None, type=float,
    help="Override contamination (fixed mode, or cap for proportional).",
)
@click.option(
    "--max-ctk-fraction", default=0.10, show_default=True,
    help="Max fraction of baseline rows from CTK caches.",
)
@click.option(
    "--ctk-verdict-filter",
    type=click.Choice(["pass_only", "all", "none"], case_sensitive=False),
    default="pass_only",
    show_default=True,
    help="Which cached runs may contribute rows (default: PASS only).",
)
@click.option("--config", "config_path", default=None, help="Path to settings.yaml.")
def retrain_anomaly_cmd(
    export_paths,
    ctk_runs_dir,
    output_path,
    contamination_mode,
    fixed_contamination,
    max_ctk_fraction,
    ctk_verdict_filter,
    config_path,
):
    """Retrain IsolationForest on baseline exports with optional capped CTK windows."""
    from sklearn.ensemble import IsolationForest

    from chaosgen.config.settings import load_settings
    from chaosgen.evaluation.anomaly_corpus import (
        CtkVerdictFilter,
        ContaminationMode,
        build_retrain_corpus,
    )
    from chaosgen.ml.anomaly_detector import AnomalyDetector
    from chaosgen.ml.canonical_features import CANONICAL_SCHEMA_VERSION
    from chaosgen.ml.cluster_labels import ClusterLabelStore

    settings = load_settings(config_path)
    mode = ContaminationMode(contamination_mode.lower())
    verdict_filter = CtkVerdictFilter(ctk_verdict_filter.lower())

    corpus = build_retrain_corpus(
        export_paths,
        settings,
        include_ctk_runs_dir=ctk_runs_dir,
        verdict_filter=verdict_filter,
        contamination_mode=mode,
        max_ctk_fraction=max_ctk_fraction,
        fixed_contamination=fixed_contamination,
    )

    click.echo(
        f"[ChaosGen] Corpus: {len(corpus.features)} rows "
        f"(baseline={corpus.baseline_rows}, ctk={corpus.ctk_rows_used}/{corpus.ctk_rows_requested})"
    )
    click.echo(
        f"[ChaosGen] Contamination: {corpus.contamination:.4f} ({corpus.contamination_mode.value})"
    )
    for note in corpus.notes:
        click.secho(f"  note: {note}", fg="yellow")

    detector = AnomalyDetector(settings=settings.anomaly)
    detector.contamination = corpus.contamination
    detector.iso_forest = IsolationForest(
        contamination=corpus.contamination,
        random_state=detector.random_state,
        n_jobs=-1,
    )
    if settings.features.canonical_enabled:
        detector.canonical_schema_version = CANONICAL_SCHEMA_VERSION

    click.echo(
        f"[ChaosGen] Fitting IsolationForest on {len(corpus.features)} samples, "
        f"{corpus.features.shape[1]} features..."
    )
    detector.fit(corpus.features)

    clusters = []
    try:
        detected = detector.detect(corpus.features)
        if isinstance(detected, list):
            clusters = detected
    except Exception as exc:
        click.secho(f"Warning during KMeans fit: {exc}", fg="yellow")

    detector.save_model(output_path)
    click.echo(f"[ChaosGen] Model saved: {output_path}")

    cluster_ids = [c.cluster_id for c in clusters]
    if not cluster_ids and getattr(detector, "last_chosen_k", None):
        cluster_ids = list(range(int(detector.last_chosen_k)))
    if cluster_ids:
        store = ClusterLabelStore.sidecar_for_model(output_path)
        label_path = store.write_stub(cluster_ids, model_path=output_path)
        click.echo(f"[ChaosGen] Cluster label stub: {label_path}")

    click.secho("Retrain completed.", fg="green")


@main.command("plot-anomalies")
@click.option(
    "--export", "export_path", required=True, type=click.Path(exists=True),
    help="Path to a reference telemetry export bundle, CSV, or zip/tar.",
)
@click.option(
    "--model-path", "model_path", required=True, type=click.Path(exists=True),
    help="Path to the pre-trained ML model joblib file.",
)
@click.option(
    "--output-plot", "output_path", default="./anomaly_timeline.png",
    help="Output file path for the generated anomaly timeline plot (e.g. .png).",
)
@click.option("--config", "config_path", default=None, help="Path to settings.yaml.")
def plot_anomalies(export_path, model_path, output_path, config_path):
    """Plot the anomaly timeline, highlighting anomaly clusters over time."""
    from chaosgen.config.settings import load_settings
    from chaosgen.ingestion.export_loader import ExportLoader
    from chaosgen.ml.feature_engineering import FeatureEngineer
    from chaosgen.ml.anomaly_detector import AnomalyDetector
    from chaosgen.ml.canonical_features import apply_canonical_features

    settings = load_settings(config_path)

    click.echo(f"[ChaosGen] Loading offline export: {export_path}")
    loader = ExportLoader.resolve_bundle(export_path)
    dataset = loader.load()

    click.echo(
        f"  Series: {len(dataset.metrics)} | "
        f"Samples: {dataset.total_samples} | "
        f"Log streams: {len(dataset.logs)}"
    )

    click.echo("[ChaosGen] Running Feature Engineering...")
    fe = FeatureEngineer(settings=settings.features)
    features = fe.transform(dataset)
    features = apply_canonical_features(features, settings.features)
    if features.empty:
        raise click.ClickException("No feature data extracted from dataset; aborting plot.")

    click.echo(f"[ChaosGen] Loading pre-trained model from: {model_path}")
    detector = AnomalyDetector()
    detector.load_model(model_path)

    click.echo(f"[ChaosGen] Generating timeline plot at: {output_path}")
    detector.plot_timeline(features, output_path)
    click.secho("Timeline plot generated successfully.", fg="green")



def _describe_status(report, cluster_id: int) -> str:
    """Human-readable describe outcome for a gatekeeper candidate row."""
    for desc in report.descriptions:
        if desc.source_incident_id == cluster_id:
            if desc.metadata.get("describe_fallback"):
                return "describe fallback (no chaos)"
            if desc.knowledge_state.value == "described":
                return "described"
            return desc.knowledge_state.value
    return "—"


def _print_gatekeeper_table(report, *, show_transient: bool = False) -> None:
    from chaosgen.schemas.incidents import IncidentVerdict

    click.secho("\nAnomaly Gatekeeper Results:", bold=True)
    if not report.incident_candidates and report.filtered_noise_count == 0:
        click.echo("  (no anomaly clusters)")
        return

    noise_shown = report.filtered_noise_count
    if noise_shown:
        click.echo(f"  filtered NOISE clusters: {noise_shown}")

    for cand in report.incident_candidates:
        if cand.verdict == IncidentVerdict.TRANSIENT and not show_transient:
            continue
        log_yes = "yes" if cand.log_correlated else "no"
        verdict = cand.verdict.value.upper()
        if cand.passes_downstream:
            status = _describe_status(report, cand.cluster_id)
            arrow = f"→ {status}"
        elif cand.verdict == IncidentVerdict.TRANSIENT:
            arrow = "→ monitor only"
        else:
            arrow = "→ filtered"
        click.echo(
            f"  cluster {cand.cluster_id}  {verdict:<9} "
            f"freq={cand.frequency:.1f}/h  sev={cand.severity:.2f}  "
            f"log={log_yes}  {arrow}"
        )


def _resolve_experiment(report, experiment_arg: str):
    """Match experiment by name or numeric index within the report."""
    experiments = report.generated_experiments
    if not experiments:
        raise click.ClickException("Report contains no generated experiments.")

    if experiment_arg.isdigit():
        idx = int(experiment_arg)
        if idx < 0 or idx >= len(experiments):
            raise click.ClickException(
                f"Experiment index {idx} out of range (0–{len(experiments) - 1})"
            )
        return experiments[idx]

    if experiment_arg.startswith("ai-exp-"):
        try:
            idx = int(experiment_arg.split("-")[-1])
            return experiments[idx]
        except (ValueError, IndexError) as exc:
            raise click.ClickException(f"Invalid experiment id {experiment_arg!r}") from exc

    for exp in experiments:
        if exp.name == experiment_arg:
            return exp
    names = ", ".join(e.name for e in experiments)
    raise click.ClickException(
        f"Experiment {experiment_arg!r} not found. Available: {names}"
    )


def _generate_from_analysis(
    settings,
    clusters,
    summaries,
    lookback_hours,
    provider,
    model,
    top_n,
    output,
    config_path,
    skip_gatekeeper=False,
    show_transient=False,
    save_report_path=None,
    confidence_threshold=None,
):
    """Run shared advisor pipeline and print ranked scenarios."""
    from chaosgen.advisor.context_builder import ContextBuilder
    from chaosgen.advisor.pipeline import run_advisor_pipeline
    from chaosgen.advisor.report_store import default_report_path, save_report
    from chaosgen.advisor.scenario_ranker import ScenarioRanker
    from chaosgen.config.scope import scope_notice
    from chaosgen.discovery import resolve_discovery_report
    from chaosgen.schemas.scenarios import AdvisorReport
    from chaosgen.storage.history import get_default_history_store

    effective_provider = provider or settings.llm_provider
    effective_model = model or settings.llm_model
    if effective_provider:
        settings = settings.model_copy(
            update={"llm_provider": effective_provider, "llm_model": effective_model}
        )

    click.echo("\n[ChaosGen] Generating scenarios from analysis...")
    click.echo(f"  {scope_notice()}")
    discovery = resolve_discovery_report(settings=settings)
    ctx = ContextBuilder(discovery).build()

    if not summaries:
        click.secho("No anomaly summaries — cannot run advisor pipeline.", fg="yellow")
        return

    if skip_gatekeeper:
        click.secho("  WARNING: --skip-gatekeeper enabled (debug only)", fg="yellow")

    report_path = save_report_path or str(default_report_path())
    report = run_advisor_pipeline(
        clusters,
        summaries,
        settings=settings,
        lookback_hours=lookback_hours,
        context=ctx,
        skip_gatekeeper=skip_gatekeeper,
        generate_chaos=True,
        report_path=report_path,
        confidence_threshold=confidence_threshold,
    )
    _print_gatekeeper_table(report, show_transient=show_transient)

    written = save_report(report, save_report_path)
    if save_report_path:
        click.echo(f"\n  Report saved to {written}")
    else:
        click.echo(f"\n  Report saved to {default_report_path()}")

    if not report.generated_experiments:
        click.secho(
            "No chaos scenarios generated (gatekeeper/describe filter may have removed all).",
            fg="yellow",
        )
        return

    effective_top_n = top_n if top_n is not None else settings.advisor.top_n_scenarios
    ranker = ScenarioRanker(
        history_store=get_default_history_store(settings),
        settings=settings.ranking,
    )
    sources = {exp.name: "llm" for exp in report.generated_experiments}
    ranked = ranker.rank(
        report.generated_experiments, sources=sources, top_n=effective_top_n
    )
    if not ranked:
        click.secho("No scenarios ranked.", fg="yellow")
        return

    click.secho(f"\nTop {len(ranked)} ranked scenarios:\n", bold=True)
    for r in ranked:
        click.echo(f"  {r.summary()}")


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--tier",
    type=click.Choice(["auto", "k8s", "docker", "script", "terraform"]),
    default="auto", show_default=True,
    help="Force a specific install tier (auto = use detected environment).",
)
@click.option("--namespace", default="monitoring", show_default=True, help="K8s namespace for Tier 1.")
@click.option("--output-dir", default=".", show_default=True, help="Dir for generated scripts (Tier 3/4).")
def bootstrap(tier, namespace, output_dir):
    """Install or generate observability tooling (Prometheus, Grafana, Loki)."""
    from chaosgen.bootstrap import ObservabilityInstaller
    from chaosgen.config.scope import scope_notice
    from chaosgen.discovery import resolve_discovery_report
    from chaosgen.schemas.discovery import EnvironmentType

    click.echo(f"[ChaosGen] Resolving pipeline context (microservices focus)...")
    click.echo(f"  {scope_notice()}")
    report = resolve_discovery_report()

    if tier != "auto":
        env_map = {
            "k8s": EnvironmentType.KUBERNETES,
            "docker": EnvironmentType.DOCKER_COMPOSE,
            "script": EnvironmentType.BARE_METAL,
            "terraform": EnvironmentType.SERVERLESS,
        }
        report.environment.type = env_map[tier]

    try:
        installer = ObservabilityInstaller(
            env_profile=report.environment,
            obs_profile=report.observability,
            namespace=namespace,
            output_dir=output_dir,
        )
        actions = installer.install()
        for action in actions:
            click.secho(f"  OK: {action}", fg="green")
    except Exception as exc:
        click.secho(f"Bootstrap error: {exc}", fg="red")
        sys.exit(1)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--provider",
    type=click.Choice(["ollama", "openai", "anthropic", "groq"]),
    default=None, help="LLM provider (default: from settings.yaml or 'ollama').",
)
@click.option("--model", default=None, help="Override default model for the chosen provider.")
@click.option(
    "--arch", default=None,
    type=click.Choice(["auto", "microservices", "monolith", "modular_monolith", "event_driven", "client_server", "serverless"]),
    help="Architecture type override.",
)
@click.option("--top-n", default=None, type=int, help="Number of scenarios to return (default: settings.advisor.top_n_scenarios).")
@click.option(
    "--confidence-threshold",
    default=None,
    type=float,
    help="Drop LLM hypotheses below this confidence (default: settings.advisor.confidence_threshold).",
)
@click.option("--from-catalog", is_flag=True, default=False, help="Use pre-built catalog instead of LLM.")
@click.option(
    "--output", type=click.Choice(["table", "json", "yaml"]),
    default="table", show_default=True,
)
@click.option("--skip-gatekeeper", is_flag=True, default=False, help="Debug: bypass gatekeeper filter.")
@click.option("--show-transient", is_flag=True, default=False, help="Show TRANSIENT rows in gatekeeper table.")
@click.option("--save-report", "save_report_path", default=None, help="Write AdvisorReport JSON to PATH.")
@click.option("--config", "config_path", default=None, help="Path to settings.yaml.")
@click.option("--model-path", default=None, help="Path to pre-trained ML model joblib file.")
@click.option(
    "--export", "export_path", default=None, type=click.Path(exists=True),
    help="Offline export bundle, CSV, or zip/tar (window from metadata or timestamps).",
)
@click.option("--hours", default=None, type=int, help="Lookback hours for live collect (default: settings or 24).")
@click.option(
    "--start", type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]), default=None,
    help="Absolute start datetime (UTC) for live collection.",
)
@click.option(
    "--end", type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]), default=None,
    help="Absolute end datetime (UTC) for live collection.",
)
def generate(
    provider, model, arch, top_n, confidence_threshold, from_catalog, output, skip_gatekeeper, show_transient,
    save_report_path, config_path, model_path, export_path, hours, start, end,
):
    """Generate AI chaos scenarios from anomaly data or the pre-built catalog."""
    from chaosgen.advisor.scenario_catalog import ScenarioCatalog
    from chaosgen.advisor.scenario_ranker import ScenarioRanker
    from chaosgen.config.scope import FOCUSED_ARCHITECTURE, scope_notice
    from chaosgen.config.settings import load_settings
    from chaosgen.discovery import resolve_discovery_report
    from chaosgen.schemas.discovery import ArchitectureType

    settings = load_settings(config_path)
    effective_provider = provider or settings.llm_provider
    effective_model = model or settings.llm_model

    click.echo("[ChaosGen] Resolving pipeline context...")
    click.echo(f"  {scope_notice()}")
    report = resolve_discovery_report(settings=settings)

    if arch and arch != "auto":
        requested = ArchitectureType(arch)
        if requested != FOCUSED_ARCHITECTURE:
            click.secho(
                f"  Note: only {FOCUSED_ARCHITECTURE.value} is in active scope; "
                f"using catalog/advisor for {requested.value} anyway.",
                fg="yellow",
            )
        report.architecture.type = requested

    arch_type = report.architecture.type
    click.echo(f"  Architecture: {arch_type.value}")

    if from_catalog:
        catalog = ScenarioCatalog()
        entries = catalog.get_all(arch_type)
        experiments = [e.build() for e in entries]
        sources = {e.build().name: "catalog" for e in entries}
    else:
        from chaosgen.advisor.context_builder import ContextBuilder
        from chaosgen.advisor.pipeline import run_advisor_pipeline
        from chaosgen.advisor.report_store import default_report_path, save_report
        from chaosgen.advisor.scenario_ranker import ScenarioRanker
        from chaosgen.ingestion.analysis import analyze_dataset
        from chaosgen.ingestion.export_loader import ExportLoader
        from chaosgen.ingestion.telemetry_factory import build_telemetry_collector
        from chaosgen.ingestion.window import resolve_collection_window

        click.echo(f"  Provider: {effective_provider}")
        if skip_gatekeeper:
            click.secho("  WARNING: --skip-gatekeeper enabled (debug only)", fg="yellow")

        # MODIFIED: P7 — same window contract as analyze (no hardcoded 24h)
        if hours is not None and (start is not None or end is not None):
            raise click.ClickException(
                "Use either --hours (relative) or --start/--end (absolute), not both."
            )
        if (start is None) ^ (end is None):
            raise click.ClickException("Both --start and --end are required for absolute windows.")

        ctx = ContextBuilder(report).build()

        try:
            if export_path:
                click.echo(f"  Loading export: {export_path}")
                loader = ExportLoader.resolve_bundle(export_path)
                dataset = loader.load()
                window = resolve_collection_window(dataset=dataset, settings=settings)
            else:
                window = resolve_collection_window(
                    hours=hours, start=start, end=end, settings=settings,
                )
                collector = build_telemetry_collector(settings)
                click.echo(
                    f"  Collecting live telemetry "
                    f"({window.start.isoformat()} -> {window.end.isoformat()}, "
                    f"{window.lookback_hours:.1f}h)..."
                )
                if start and end:
                    dataset = collector.collect_range(
                        start=window.start, end=window.end, step=settings.telemetry.step,
                    )
                else:
                    dataset = collector.collect_baseline(
                        duration_hours=int(round(window.lookback_hours)),
                        step=settings.telemetry.step,
                    )
            clusters, anomaly_summaries, _ = analyze_dataset(
                dataset, model_path=model_path, settings=settings,
            )
        except Exception as exc:
            click.secho(f"  Telemetry unavailable ({exc}), cannot run pipeline.", fg="red")
            sys.exit(1)

        if not anomaly_summaries:
            click.secho("No anomalies detected.", fg="yellow")
            return

        advisor_report = run_advisor_pipeline(
            clusters,
            anomaly_summaries,
            settings=settings,
            lookback_hours=float(window.lookback_hours),
            context=ctx,
            skip_gatekeeper=skip_gatekeeper,
            generate_chaos=True,
            confidence_threshold=confidence_threshold,
        )
        _print_gatekeeper_table(advisor_report, show_transient=show_transient)
        written = save_report(advisor_report, save_report_path)
        click.echo(f"\n  Report saved to {written if save_report_path else default_report_path()}")

        experiments = advisor_report.generated_experiments
        sources = {exp.name: "llm" for exp in experiments}

    from chaosgen.storage.history import get_default_history_store

    effective_top_n = top_n if top_n is not None else settings.advisor.top_n_scenarios
    ranker = ScenarioRanker(
        history_store=get_default_history_store(settings),
        settings=settings.ranking,
    )
    ranked = ranker.rank(experiments, sources=sources, top_n=effective_top_n)

    if not ranked:
        click.secho("No scenarios generated.", fg="yellow")
        return

    if output == "json":
        result = [
            {"name": r.experiment.name, "rank_score": r.rank_score,
             "rank_reason": r.rank_reason, "source": r.source}
            for r in ranked
        ]
        click.echo(json.dumps(result, indent=2))
        return

    if output == "yaml":
        import yaml
        for r in ranked:
            click.echo(yaml.dump(r.experiment.model_dump(mode="json"), default_flow_style=False))
            click.echo("---")
        return

    click.secho(f"\nTop {len(ranked)} ranked scenarios for '{arch_type.value}':\n", bold=True)
    for i, r in enumerate(ranked, 1):
        color = "green" if r.rank_score >= 0.7 else ("yellow" if r.rank_score >= 0.4 else "red")
        click.secho(f"  [{i}] [{r.rank_score:.2f}] {r.experiment.name}  ({r.source})", fg=color)
        click.echo(f"       -> {r.rank_reason}")

    click.echo(f"\nRun `chaosgen run` to execute approved scenarios.")


# ---------------------------------------------------------------------------
# incidents
# ---------------------------------------------------------------------------


def _parse_since_duration(value: str):
    """Parse durations like ``7d``, ``24h``, ``30m`` into a timedelta."""
    from datetime import datetime, timedelta, timezone

    value = value.strip().lower()
    if value.endswith("d"):
        delta = timedelta(days=int(value[:-1]))
    elif value.endswith("h"):
        delta = timedelta(hours=int(value[:-1]))
    elif value.endswith("m"):
        delta = timedelta(minutes=int(value[:-1]))
    else:
        raise click.ClickException(
            f"Invalid --since value {value!r}; use e.g. 7d, 24h, 30m"
        )
    return datetime.now(timezone.utc) - delta


@main.command("incidents")
@click.option(
    "--from-report", "report_path", default=None,
    help="AdvisorReport JSON snapshot (P4). When set, queries the report instead of history.db.",
)
@click.option(
    "--state",
    type=click.Choice(["unknown", "described", "known"]),
    default=None,
    help="Filter by ScenarioKnowledgeState.",
)
@click.option("--run-id", type=int, default=None, help="Filter history.db descriptions by analysis run id.")
@click.option("--chronic", is_flag=True, help="Show recurring REAL/CHRONIC patterns from history.db.")
@click.option("--since", default="7d", show_default=True, help="Lookback for --chronic (e.g. 7d, 24h).")
@click.option("--output", type=click.Choice(["table", "json"]), default="table", show_default=True)
def incidents(report_path, state, run_id, chronic, since, output):
    """List incidents from history.db (default) or a saved advisor report."""
    from chaosgen.advisor.report_store import default_report_path, load_report
    from chaosgen.schemas.scenarios import ScenarioKnowledgeState
    from chaosgen.storage.history import get_default_history_store

    if report_path is not None:
        path = report_path or str(default_report_path())
        try:
            report = load_report(path)
        except FileNotFoundError as exc:
            raise click.ClickException(
                f"{exc}. Run `chaosgen generate` or `chaosgen analyze --with-generate` first."
            ) from exc

        descriptions = list(report.descriptions)
        if state:
            target = ScenarioKnowledgeState(state)
            descriptions = [d for d in descriptions if d.knowledge_state == target]

        if output == "json":
            click.echo(json.dumps([d.model_dump(mode="json") for d in descriptions], indent=2))
            return

        if not descriptions:
            click.secho("No incidents match the filter.", fg="yellow")
            return

        click.secho(f"\nIncidents from report ({len(descriptions)}):\n", bold=True)
        for desc in descriptions:
            fallback = " [fallback]" if desc.metadata.get("describe_fallback") else ""
            click.echo(
                f"  [{desc.source_incident_id}] {desc.knowledge_state.value}{fallback}: "
                f"{desc.title}"
            )
        return

    store = get_default_history_store()
    if store is None:
        raise click.ClickException(
            "History store disabled. Set history.enabled in settings or use --from-report."
        )

    if chronic:
        since_dt = _parse_since_duration(since)
        patterns = store.get_chronic_patterns(since_dt)
        if output == "json":
            click.echo(
                json.dumps(
                    [
                        {
                            "service_target": p.service_target,
                            "error_pattern": p.error_pattern,
                            "occurrence_count": p.occurrence_count,
                            "last_seen": p.last_seen.isoformat(),
                        }
                        for p in patterns
                    ],
                    indent=2,
                )
            )
            return
        if not patterns:
            click.secho("No chronic patterns in the selected window.", fg="yellow")
            return
        click.secho(f"\nChronic patterns since {since}:\n", bold=True)
        for p in patterns:
            click.echo(
                f"  {p.service_target} | {p.error_pattern or '(no pattern)'} "
                f"| count={p.occurrence_count} | last={p.last_seen.isoformat()}"
            )
        return

    state_filter = ScenarioKnowledgeState(state) if state else None
    rows = store.list_descriptions(run_id=run_id, state=state_filter)
    if output == "json":
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.secho("No incidents match the filter.", fg="yellow")
        return

    click.secho(f"\nIncidents from history ({len(rows)}):\n", bold=True)
    for row in rows:
        fallback = " [fallback]" if row["describe_fallback"] else ""
        promoted = f" → {row['promoted_catalog_name']}" if row["promoted_catalog_name"] else ""
        click.echo(
            f"  [run={row['run_id']} id={row['id']} incident={row['source_incident_id']}] "
            f"{row['knowledge_state']}{fallback}: {row['title']}{promoted}"
        )


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------


@main.command("promote")
@click.option("--from-report", "report_path", required=True, help="AdvisorReport JSON from generate/analyze.")
@click.option("--approved-by", required=True, help="HITL operator sign-off (required).")
@click.option("--criteria-file", required=True, type=click.Path(exists=True), help="YAML/JSON acceptance criteria.")
@click.option("--incident-id", type=int, default=None, help="Match description.source_incident_id in report.")
@click.option("--experiment", required=True, help="Experiment name or numeric index in report.")
@click.option(
    "--verdict",
    type=click.Choice(["pass", "fail", "partial"], case_sensitive=False),
    default=None,
    help="Expectation Verdict for this run (must be pass to promote).",
)
@click.option(
    "--from-verdict",
    "verdict_path",
    type=click.Path(exists=True),
    default=None,
    help="ExpectationVerdictReport JSON (verdict field used; must be pass).",
)
def promote(report_path, approved_by, criteria_file, incident_id, experiment, verdict, verdict_path):
    """Promote a described incident into the dynamic catalog (HITL + PASS-gated)."""
    import yaml

    from chaosgen.advisor.catalog_promoter import CatalogPromoter, PromoteError
    from chaosgen.advisor.report_store import load_report, load_verdict_report
    from chaosgen.schemas.scenarios import ExperimentVerdict, ScenarioKnowledgeState
    from chaosgen.storage.history import get_default_history_store

    if verdict is None and verdict_path is None:
        raise click.ClickException(
            "Provide --verdict pass|fail|partial or --from-verdict <ExpectationVerdictReport JSON>."
        )

    if verdict_path is not None:
        report_v = load_verdict_report(verdict_path)
        exp_verdict = report_v.verdict
    else:
        exp_verdict = ExperimentVerdict(verdict.lower())

    try:
        report = load_report(report_path)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    descriptions = report.descriptions
    if incident_id is not None:
        descriptions = [d for d in descriptions if d.source_incident_id == incident_id]
    if not descriptions:
        raise click.ClickException(
            "No matching description in report"
            + (f" for incident-id {incident_id}" if incident_id is not None else "")
        )
    if len(descriptions) > 1:
        raise click.ClickException(
            "Multiple descriptions match; specify --incident-id to disambiguate."
        )
    description = descriptions[0]

    experiment_obj = _resolve_experiment(report, experiment)

    raw = Path(criteria_file).read_text(encoding="utf-8")
    if criteria_file.endswith((".yaml", ".yml")):
        criteria = yaml.safe_load(raw) or {}
    else:
        criteria = json.loads(raw)

    description_row_id = report.description_db_ids.get(description.source_incident_id)

    from chaosgen.config.connect_routing import architecture_from_settings
    from chaosgen.config.settings import load_settings

    promoter = CatalogPromoter(history_store=get_default_history_store())
    promote_arch = architecture_from_settings(load_settings())
    try:
        entry = promoter.promote(
            description,
            experiment_obj,
            approved_by=approved_by,
            verdict=exp_verdict,
            acceptance_criteria=criteria,
            name=experiment_obj.name,
            description_row_id=description_row_id,
            architecture=promote_arch,
        )
    except PromoteError as exc:
        raise click.ClickException(str(exc)) from exc

    click.secho(f"Promoted '{entry.name}' to catalog (source=promoted).", fg="green")
    if description.knowledge_state == ScenarioKnowledgeState.KNOWN:
        click.echo(f"  Incident {description.source_incident_id} marked KNOWN.")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@main.command()
@click.option("--dry-run", is_flag=True, default=False, help="Validate without executing.")
@click.option("--approve-all", is_flag=True, default=False, help="Skip HITL gate (requires --force).")
@click.option("--force", is_flag=True, default=False, hidden=True)
@click.option("--config", default=None, help="Path to config YAML.")
def run(dry_run, approve_all, force, config):
    """Execute approved chaos experiments through the HITL gate."""
    from chaosgen.orchestrator import ChaosOrchestrator

    orchestrator = ChaosOrchestrator(config_path=config)

    if not orchestrator.pending_experiments:
        click.secho("No experiments in the approval queue. Run `chaosgen generate` first.", fg="yellow")
        return

    click.echo(f"Pending experiments: {len(orchestrator.pending_experiments)}")
    for i, exp in enumerate(orchestrator.pending_experiments):
        click.echo(f"  [{i}] {exp.name}")

    if dry_run:
        click.secho("Dry-run mode — no experiments will be executed.", fg="cyan")
        return

    if approve_all and not force:
        click.secho("--approve-all requires --force to bypass HITL gate. Add --force to confirm.", fg="red")
        return

    for i, exp in enumerate(orchestrator.pending_experiments):
        if approve_all and force:
            do_run = True
        else:
            do_run = click.confirm(f"Approve and run '{exp.name}'?", default=False)

        if do_run:
            click.echo(f"Running: {exp.name}")
            orchestrator.approve_and_run(i)
        else:
            click.echo(f"Skipped: {exp.name}")


# ---------------------------------------------------------------------------
# verdict  (P0-B — operational expectation evaluation)
# ---------------------------------------------------------------------------


@main.command("verdict")
@click.option(
    "--criteria", "criteria_file", default=None, type=click.Path(exists=True),
    help="YAML/JSON with claim + expectations (see examples/demo-expectation-criteria.yaml).",
)
@click.option(
    "--journal", "journal_path", default=None, type=click.Path(exists=True),
    help="Evaluate from a CTK journal JSON (post chaos run).",
)
@click.option(
    "--prometheus-url", default=None,
    help="Override Prometheus base URL for threshold checks.",
)
@click.option(
    "--no-poll", is_flag=True, default=False,
    help="Evaluate once without waiting window_seconds (faster dry checks).",
)
@click.option(
    "--experiment-name", default=None,
    help="Label for the verdict report (optional).",
)
@click.option(
    "--save", "save_path", default=None,
    help="Write ExpectationVerdictReport JSON (default: config last_verdict.json).",
)
def verdict_cmd(criteria_file, journal_path, prometheus_url, no_poll, experiment_name, save_path):
    """Evaluate operational expectations or a CTK journal; print PASS/FAIL/PARTIAL."""
    import yaml
    from chaosgen.advisor.catalog_promoter import (
        evaluate_acceptance_detailed,
        validate_acceptance_criteria,
    )
    from chaosgen.advisor.report_store import save_verdict_report
    from chaosgen.config.telemetry_endpoints import resolve_prometheus_url
    from chaosgen.config.settings import load_settings
    from chaosgen.evaluation.ctk_verdict import build_verdict_from_ctk_run

    settings = load_settings()
    prom = prometheus_url
    if not prom:
        try:
            prom = resolve_prometheus_url(settings)
        except Exception:
            prom = None

    if journal_path:
        criteria = None
        if criteria_file:
            raw = Path(criteria_file).read_text(encoding="utf-8")
            if str(criteria_file).endswith((".yaml", ".yml")):
                criteria = yaml.safe_load(raw) or {}
            else:
                criteria = json.loads(raw)
            errors = validate_acceptance_criteria(criteria)
            if errors:
                raise click.ClickException("Invalid criteria:\n  - " + "\n  - ".join(errors))
        report = build_verdict_from_ctk_run(
            {"success": True, "journal_path": str(journal_path)},
            experiment_name=experiment_name,
            acceptance_criteria=criteria,
            prometheus_url=prom,
            poll_telemetry=not no_poll,
        )
    elif criteria_file:
        raw = Path(criteria_file).read_text(encoding="utf-8")
        if str(criteria_file).endswith((".yaml", ".yml")):
            criteria = yaml.safe_load(raw) or {}
        else:
            criteria = json.loads(raw)

        errors = validate_acceptance_criteria(criteria)
        if errors:
            raise click.ClickException("Invalid criteria:\n  - " + "\n  - ".join(errors))

        report = evaluate_acceptance_detailed(
            criteria,
            experiment_name=experiment_name,
            poll=not no_poll,
            prometheus_url=prom,
        )
    else:
        raise click.ClickException("Provide --journal PATH or --criteria FILE.")

    written = save_verdict_report(report, save_path)

    color = {
        "pass": "green",
        "partial": "yellow",
        "fail": "red",
    }.get(report.verdict.value, "white")

    click.secho(f"\n=== Operational Verdict: {report.verdict.value.upper()} ===", fg=color, bold=True)
    click.echo(f"Claim: {report.claim.strip()}")
    click.echo(f"Rationale: {report.rationale}")
    click.echo("Checks:")
    for c in report.checks:
        mark = "PASS" if c.passed else "FAIL"
        click.echo(f"  [{mark}] {c.id}: {c.message}")
    click.echo(f"\nSaved: {written}")
    click.echo("See docs/advisor-demo-verdict-beat.md for advisor demo narration.")


# ---------------------------------------------------------------------------
# fetch-run-telemetry  (Phase 1 — anomaly retrain window cache)
# ---------------------------------------------------------------------------


@main.command("fetch-run-telemetry")
@click.option(
    "--journal", "journal_path", default=None, type=click.Path(exists=True),
    help="CTK journal JSON (chaos run --journal-path output).",
)
@click.option(
    "--verdict", "verdict_path", default=None, type=click.Path(exists=True),
    help="ExpectationVerdictReport JSON (joins journal_path from metadata).",
)
@click.option(
    "--journal-dir", default=None, type=click.Path(exists=True),
    help="Scan directory of journal *.json files (batch cache).",
)
@click.option(
    "--padding", default=60, show_default=True,
    help="Seconds to expand window before start and after end.",
)
@click.option(
    "--output-dir", default="scratch/telemetry/runs", show_default=True,
    help="Directory for cached telemetry JSON bundles.",
)
@click.option("--config", "config_path", default=None, help="Path to settings.yaml.")
@click.option("--prometheus-url", default=None, help="Override Prometheus base URL.")
@click.option("--loki-url", default=None, help="Override Loki base URL.")
@click.option(
    "--dry-run", is_flag=True, default=False,
    help="Print resolved windows only; do not query Prometheus/Loki.",
)
def fetch_run_telemetry_cmd(
    journal_path,
    verdict_path,
    journal_dir,
    padding,
    output_dir,
    config_path,
    prometheus_url,
    loki_url,
    dry_run,
):
    """Fetch Prometheus/Loki telemetry for CTK journal time windows and cache locally."""
    from chaosgen.evaluation.run_catalog import build_run_records, RunCatalogError
    from chaosgen.evaluation.run_telemetry import fetch_and_cache_run

    if not journal_path and not verdict_path and not journal_dir:
        raise click.ClickException(
            "Provide --journal, --verdict, and/or --journal-dir."
        )

    try:
        records = build_run_records(
            journal_paths=[journal_path] if journal_path else None,
            journal_dir=journal_dir,
            verdict_path=verdict_path,
            padding_seconds=padding,
        )
    except RunCatalogError as exc:
        raise click.ClickException(str(exc)) from exc

    if not records:
        raise click.ClickException("No run records resolved from inputs.")

    for record in records:
        click.echo(
            f"\nRun: {record.run_id} | {record.experiment_name} | "
            f"window {record.window_start.isoformat()} -> {record.window_end.isoformat()} "
            f"({record.window_source.value}, pad={record.window_padding_seconds}s)"
        )
        if record.verdict:
            click.echo(f"  verdict: {record.verdict.value}")
        for w in record.warnings:
            click.secho(f"  warning: {w}", fg="yellow")
        if record.dry_run:
            click.secho("  skipped: dry_run flag on record", fg="yellow")
            continue
        if dry_run:
            continue
        updated = fetch_and_cache_run(
            record,
            output_dir=output_dir,
            config_path=config_path,
            prom_url=prometheus_url,
            loki_url=loki_url,
        )
        click.echo(f"  cached: {updated.telemetry_cache_path}")


# ---------------------------------------------------------------------------
# eval-anomaly-alignment  (Phase 3 — IF vs verdict validation)
# ---------------------------------------------------------------------------


@main.command("eval-anomaly-alignment")
@click.option(
    "--runs-dir", default="scratch/telemetry/runs", show_default=True,
    help="Directory of cached per-run telemetry JSON bundles.",
)
@click.option(
    "--model-path", default=None, type=click.Path(exists=True),
    help="Pre-trained IsolationForest .joblib (default: settings anomaly.default_model_path).",
)
@click.option(
    "--verdict", "verdict_path", default=None, type=click.Path(exists=True),
    help="Optional verdict JSON to enrich cached runs missing labels.",
)
@click.option(
    "--journal-dir", default=None, type=click.Path(exists=True),
    help="Optional journal directory to enrich verdict labels on cached runs.",
)
@click.option(
    "--output-md", default="docs/anomaly-verdict-alignment.md", show_default=True,
    help="Markdown report for thesis slides.",
)
@click.option(
    "--output-json", default="docs/anomaly-verdict-alignment.json", show_default=True,
    help="Structured JSON report.",
)
@click.option("--config", "config_path", default=None, help="Path to settings.yaml.")
def eval_anomaly_alignment_cmd(
    runs_dir,
    model_path,
    verdict_path,
    journal_dir,
    output_md,
    output_json,
    config_path,
):
    """Score IF spikes vs chaos verdict labels; write Markdown + JSON alignment report."""
    from chaosgen.evaluation.verdict_alignment import (
        build_alignment_report,
        enrich_records_from_catalog,
        load_cached_runs_from_dir,
        write_alignment_artifacts,
    )

    cached = load_cached_runs_from_dir(runs_dir)
    if not cached:
        raise click.ClickException(f"No cached runs in {runs_dir}")

    if verdict_path or journal_dir:
        records = enrich_records_from_catalog(
            [r for r, _ in cached],
            verdict_path=verdict_path,
            journal_dir=journal_dir,
        )
        cached = [(records[i], cached[i][1]) for i in range(len(cached))]

    report = build_alignment_report(
        runs_dir=runs_dir,
        model_path=model_path,
        config_path=config_path,
        cached_runs=cached,
    )

    md_path, json_path = write_alignment_artifacts(
        report,
        markdown_path=output_md,
        json_path=output_json,
    )

    click.echo("\n=== Anomaly vs Verdict Alignment ===")
    click.echo(f"Evaluable runs: {report.summary.evaluable_runs}")
    click.echo(f"Alignment rate: {report.summary.alignment_rate:.2%}")
    click.echo(f"FAIL detection: {report.summary.fail_detection_rate:.2%}")
    click.echo(f"PASS specificity: {report.summary.pass_specificity:.2%}")
    click.echo(f"\nMarkdown: {md_path}")
    click.echo(f"JSON: {json_path}")
    click.echo(f"\n{report.narrative}")


# ---------------------------------------------------------------------------
# inject-gc  (label-driven orphan sweep — no local state required)
# ---------------------------------------------------------------------------


@main.command("inject-gc")
@click.option("--config", default=None, help="Path to settings YAML.")
@click.option("--kubeconfig", default=None, help="Override inject.kubeconfig.")
@click.option("--context", default=None, help="Override kubeconfig context.")
@click.option("--dry-run", is_flag=True, default=False, help="List matching CRs; do not delete.")
@click.option("--list-only", is_flag=True, default=False, help="Same as --dry-run: enumerate only.")
@click.option("--namespace", default=None, help="Limit GC to one namespace (default: all).")
def inject_gc(config, kubeconfig, context, dry_run, list_only, namespace):
    """Delete ephemeral Chaos Mesh CRs owned by ChaosGen (managed-by label)."""
    from chaosgen.orchestrator import ChaosOrchestrator

    orch = ChaosOrchestrator(config_path=config)
    kube = orch.get_module("kubectl-chaos")
    if not kube:
        raise click.ClickException("kubectl-chaos module not available")
    if kubeconfig:
        kube.kubeconfig = kube._expand(kubeconfig)
        kube.invalidate_client()
    if context:
        kube.context = context
        kube.invalidate_client()
    if dry_run or list_only:
        kube.dry_run = True
    params: dict = {}
    if namespace:
        params["namespace"] = namespace
    if list_only:
        params["list_only"] = True
    action = "list_ephemeral" if list_only else "gc_ephemeral"
    result = kube.execute(action, params)
    items = result.get("items") or []
    if items:
        click.echo(f"selector: {result.get('selector') or ''}")
        for item in items:
            click.echo(
                f"  {item.get('namespace')}/{item.get('kind')}/{item.get('name')}"
            )
    if result.get("success"):
        click.secho(
            result.get("message") or "inject-gc completed",
            fg="green",
        )
        if result.get("dry_run"):
            cmd = result.get("cmd")
            if cmd:
                click.echo(f"cmd: {' '.join(cmd)}")
        backend = result.get("backend")
        if backend:
            click.echo(f"backend: {backend}")
    else:
        raise click.ClickException(result.get("error") or result.get("message") or "inject-gc failed")


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


@main.command()
@click.option("--ab", is_flag=True, default=False, help="Show A/B comparison report.")
@click.option("--export", type=click.Choice(["json", "csv"]), default=None, help="Export report to file.")
@click.option("--config", default=None, help="Path to config YAML.")
def evaluate(ab, export, config):
    """Show KPI tracker report and optionally an A/B comparison."""
    from chaosgen.evaluation.kpi_tracker import KPITracker

    tracker = KPITracker()
    report = tracker.generate_report()

    if export == "json":
        fname = "chaosgen_kpi_report.json"
        with open(fname, "w") as f:
            json.dump(report, f, indent=2)
        click.echo(f"Exported to {fname}")
        return

    if export == "csv":
        fname = "chaosgen_kpi_report.csv"
        tracker.export_csv(fname)
        click.echo(f"Exported to {fname}")
        return

    click.secho("\n=== KPI Report ===", bold=True)
    for key, value in report.items():
        click.echo(f"  {key}: {value}")

    if ab:
        from chaosgen.evaluation.ab_comparator import ABComparator
        comparator = ABComparator(tracker)
        ab_report = comparator.compare()
        click.secho("\n=== A/B Comparison ===", bold=True)
        click.echo(ab_report)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@main.command()
@click.option("--module", default=None, help="Show status for a specific module.")
@click.option("--config", default=None, help="Path to config YAML.")
def status(module, config):
    """Show orchestrator and module status."""
    from chaosgen.orchestrator import ChaosOrchestrator

    orchestrator = ChaosOrchestrator(config_path=config)

    if module:
        s = orchestrator.get_module_status(module)
        click.echo(json.dumps(s, indent=2))
        return

    all_status = orchestrator.get_all_status()
    click.secho(f"\nOrchestrator state: {orchestrator.state.upper()}", bold=True)
    click.secho("\nModule Status:", bold=True)
    for name, s in all_status.items():
        ok = s.get("available", False)
        color = "green" if ok else "red"
        mark = "OK" if ok else "FAIL"
        click.secho(f"  {mark}  {name}", fg=color)


# ---------------------------------------------------------------------------
# config subgroup
# ---------------------------------------------------------------------------


@main.group("config")
def config_group() -> None:
    """Manage ChaosGen settings and API keys."""


@config_group.command("init")
def config_init():
    """Interactive wizard to create settings.yaml and configure API keys."""
    from chaosgen.config.paths import CONFIG_DIR, SETTINGS_FILE
    from chaosgen.config.secrets import save_secret
    from chaosgen.config.settings import (
        AuthConfig,
        ChaosGenSettings,
        ObservabilityHint,
        UserHints,
        save_settings,
    )
    from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType, ObservabilityTool
    from chaosgen.config.profile_presets import profile_priority_tier

    click.secho("\n[ChaosGen Config Wizard]\n", bold=True)

    if click.confirm("Use registry-vm preset (192.168.31.220)?", default=True):
        from chaosgen.config.telemetry_endpoints import DEFAULT_LOKI_URL, DEFAULT_PROMETHEUS_URL
        settings = ChaosGenSettings(
            hints=UserHints(
                architecture=ArchitectureType.MICROSERVICES,
                environment=EnvironmentType.KUBERNETES,
                skip_auto_detect=True,
                observability=[
                    ObservabilityHint(
                        tool=ObservabilityTool.PROMETHEUS,
                        url=DEFAULT_PROMETHEUS_URL,
                    ),
                    ObservabilityHint(
                        tool=ObservabilityTool.LOKI,
                        url=DEFAULT_LOKI_URL,
                    ),
                ],
            ),
        )
        save_settings(settings)
        click.secho(f"\nSettings written to {SETTINGS_FILE}", fg="green")
        click.echo("Run: chaosgen analyze --check")
        return

    # Architecture (form-first — no auto-detect)
    arch_choices = [t.value for t in ArchitectureType if t != ArchitectureType.UNKNOWN]
    arch_val = click.prompt(
        "Architecture type",
        type=click.Choice(arch_choices),
        default=ArchitectureType.MICROSERVICES.value,
    )
    arch = ArchitectureType(arch_val)

    # Environment (optional override)
    env_choices = [t.value for t in EnvironmentType if t != EnvironmentType.UNKNOWN]
    env_val = click.prompt(
        "Environment type (Enter = preset default for architecture)",
        type=click.Choice(["(default)"] + env_choices),
        default="(default)",
        show_default=True,
    )
    env = EnvironmentType(env_val) if env_val != "(default)" else None

    settings = ChaosGenSettings(
        hints=UserHints(
            architecture=arch,
            environment=env,
            skip_auto_detect=True,
        ),
    )

    click.echo(f"\nConnect profile ({arch_val}, tier {profile_priority_tier(arch)}):")

    if arch in (ArchitectureType.MICROSERVICES,) and (env is None or env == EnvironmentType.KUBERNETES):
        kc = click.prompt("  connect.kubernetes.kubeconfig", default="", show_default=False)
        if kc.strip():
            settings.connect.kubernetes.kubeconfig = kc.strip()
            settings.inject.kubeconfig = kc.strip()

    if arch == ArchitectureType.MODULAR_MONOLITH:
        docker_host = click.prompt(
            "  connect.docker.host",
            default="unix:///var/run/docker.sock",
        )
        settings.connect.docker.host = docker_host.strip() or None
        compose = click.prompt("  connect.docker.compose_file (optional)", default="", show_default=False)
        if compose.strip():
            settings.connect.docker.compose_file = compose.strip()

    if arch == ArchitectureType.EVENT_DRIVEN:
        broker_type = click.prompt(
            "  connect.broker.type",
            type=click.Choice(["kafka", "redpanda", "rabbitmq", "nats"]),
            default="redpanda",
        )
        settings.connect.broker.type = broker_type
        settings.connect.broker.bootstrap = click.prompt(
            "  connect.broker.bootstrap",
            default="localhost:9092",
        ).strip()

    if arch == ArchitectureType.CLIENT_SERVER:
        settings.connect.toxiproxy.api_url = click.prompt(
            "  connect.toxiproxy.api_url",
            default="http://127.0.0.1:8474",
        ).strip()

    from chaosgen.config.profile_validation import require_valid_profile_connect

    try:
        require_valid_profile_connect(settings)
    except Exception as exc:
        click.secho(f"\nProfile validation failed: {exc}", fg="red")
        raise SystemExit(1) from exc

    # Observability tools
    obs_hints: list[ObservabilityHint] = []
    for tool in ObservabilityTool:
        if not click.confirm(f"Configure {tool.value}?", default=False):
            continue
        url = click.prompt(f"  {tool.value} URL", default=f"http://localhost:{_default_port(tool)}")
        auth_type = click.prompt(
            f"  {tool.value} auth type",
            type=click.Choice(["none", "bearer", "basic", "mtls"]),
            default="none",
        )
        auth = AuthConfig(auth_type=auth_type)
        if auth_type == "bearer":
            key_name = f"{tool.value.upper()}_TOKEN"
            token = click.prompt(f"  Token value (will be stored in .env as {key_name})", hide_input=True)
            save_secret(key_name, token)
            auth.token_ref = key_name
        elif auth_type == "basic":
            auth.username = click.prompt(f"  Username")
            key_name = f"{tool.value.upper()}_PASSWORD"
            password = click.prompt(f"  Password (stored in .env as {key_name})", hide_input=True)
            save_secret(key_name, password)
            auth.password_ref = key_name
        elif auth_type == "mtls":
            auth.cert_path = click.prompt(f"  Client cert path")
            auth.key_path = click.prompt(f"  Client key path")
            auth.ca_path = click.prompt(f"  CA bundle path", default="")

        obs_hints.append(ObservabilityHint(tool=tool, url=url, auth=auth))

    # LLM provider
    llm = click.prompt(
        "LLM provider",
        type=click.Choice(["ollama", "openai", "anthropic", "groq"]),
        default="ollama",
    )
    if llm != "ollama":
        key_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "groq": "GROQ_API_KEY"}
        key_name = key_map[llm]
        api_key = click.prompt(f"  {key_name} (stored in .env)", hide_input=True)
        save_secret(key_name, api_key)

    settings = ChaosGenSettings(
        hints=UserHints(
            architecture=arch,
            environment=env,
            observability=obs_hints,
            skip_auto_detect=True,
        ),
        llm_provider=llm,
        connect=settings.connect,
    )

    save_settings(settings)
    click.secho(f"\nSettings saved to {SETTINGS_FILE}", fg="green")
    click.secho(f"Secrets saved to {CONFIG_DIR / '.env'}", fg="green")
    click.echo("Run `chaosgen discover` to validate your configuration.")


@config_group.command("show")
def config_show():
    """Display current settings.yaml content."""
    from chaosgen.config.paths import SETTINGS_FILE

    if not SETTINGS_FILE.exists():
        click.secho(f"No settings file found at {SETTINGS_FILE}.", fg="yellow")
        click.echo("Run `chaosgen config init` to create one.")
        return

    click.echo(SETTINGS_FILE.read_text(encoding="utf-8"))


@config_group.command("set-key")
@click.argument("key_name")
@click.argument("value")
def config_set_key(key_name, value):
    """Set a secret key (e.g. OPENAI_API_KEY, PROMETHEUS_TOKEN)."""
    from chaosgen.config.secrets import save_secret
    try:
        save_secret(key_name, value)
        from chaosgen.config.paths import SECRETS_FILE
        click.secho(f"Key '{key_name}' saved to {SECRETS_FILE}", fg="green")
    except ValueError as exc:
        click.secho(str(exc), fg="red")
        sys.exit(1)


@config_group.command("list-keys")
def config_list_keys():
    """List configured API keys (shows whether each is set, not the value)."""
    from chaosgen.config.secrets import load_secrets
    secrets = load_secrets()
    for k, v in secrets.items():
        status_str = "configured" if v else "not set"
        color = "green" if v else "yellow"
        click.secho(f"  {k}: {status_str}", fg=color)


def _default_port(tool) -> int:
    from chaosgen.schemas.discovery import ObservabilityTool
    ports = {
        ObservabilityTool.PROMETHEUS: 9090,
        ObservabilityTool.LOKI: 3100,
        ObservabilityTool.GRAFANA: 3000,
        ObservabilityTool.JAEGER: 16686,
        ObservabilityTool.OTEL_COLLECTOR: 13133,
    }
    return ports.get(tool, 8080)


if __name__ == "__main__":
    main()
