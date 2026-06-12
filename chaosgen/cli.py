"""
ChaosGen CLI — AI-Driven Chaos Scenario Generator

Command groups:
    chaosgen discover           [scoped off] Scan environment, architecture, observability
    chaosgen analyze            Analyze live or exported telemetry (anomaly detection)
    chaosgen bootstrap          Install missing observability tooling
    chaosgen generate           AI chaos scenario generation
    chaosgen run                Execute approved scenarios (HITL gate)
    chaosgen evaluate           KPI and A/B evaluation reports
    chaosgen status             Module and orchestrator status
    chaosgen config             Settings & API key management
"""

from __future__ import annotations

import json
import sys

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
    help="Path to an export bundle or parent exports/ directory.",
)
@click.option(
    "--check", is_flag=True, default=False,
    help="Only verify live Prometheus/Loki connectivity (Way 1).",
)
@click.option("--hours", default=24, show_default=True, help="Lookback hours for --live.")
@click.option(
    "--generate", "with_generate", is_flag=True, default=False,
    help="After analysis, run scenario generation (like chaosgen generate).",
)
@click.option("--provider", type=click.Choice(["ollama", "openai", "anthropic", "groq"]), default=None)
@click.option("--top-n", default=5, show_default=True)
@click.option("--config", "config_path", default=None, help="Path to settings.yaml.")
def analyze(live, export_path, check, hours, with_generate, provider, top_n, config_path):
    """Analyze metrics/logs from live stack or offline export; optional scenario generation."""
    from chaosgen.config.settings import load_settings
    from chaosgen.config.telemetry_endpoints import resolve_loki_url, resolve_prometheus_url
    from chaosgen.ingestion.analysis import analyze_dataset
    from chaosgen.ingestion.export_loader import ExportLoader
    from chaosgen.ingestion.telemetry_factory import build_telemetry_collector, check_live_stack

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

    if export_path:
        click.echo(f"[ChaosGen] Loading offline export: {export_path}")
        loader = ExportLoader.resolve_bundle(export_path)
        dataset = loader.load()
        source = f"export:{loader.export_root.name}"
    else:
        click.echo(f"[ChaosGen] Collecting live telemetry ({hours}h lookback)...")
        collector = build_telemetry_collector(settings)
        dataset = collector.collect_baseline(duration_hours=hours)
        source = "live"

    click.echo(
        f"  Series: {len(dataset.metrics)} | "
        f"Samples: {dataset.total_samples} | "
        f"Log streams: {len(dataset.logs)}"
    )

    clusters, summaries, feature_rows = analyze_dataset(dataset)
    click.secho(f"\n=== Anomaly Analysis ({source}) ===", bold=True)
    click.echo(f"  Feature windows: {feature_rows}")
    click.echo(f"  Anomaly clusters: {len(clusters)}")

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
        summaries=summaries,
        provider=provider,
        model=None,
        top_n=top_n,
        output="table",
        config_path=config_path,
    )


def _generate_from_analysis(
    settings,
    summaries,
    provider,
    model,
    top_n,
    output,
    config_path,
):
    """Generate ranked scenarios from precomputed anomaly summaries."""
    from chaosgen.advisor.context_builder import ContextBuilder
    from chaosgen.advisor.llm_advisor import LLMAdvisor, build_provider
    from chaosgen.advisor.scenario_generator import ScenarioGenerator
    from chaosgen.advisor.scenario_ranker import ScenarioRanker
    from chaosgen.config.scope import scope_notice
    from chaosgen.discovery import resolve_discovery_report

    effective_provider = provider or settings.llm_provider
    effective_model = model or settings.llm_model

    click.echo("\n[ChaosGen] Generating scenarios from analysis...")
    click.echo(f"  {scope_notice()}")
    report = resolve_discovery_report(settings=settings)

    try:
        llm_provider = build_provider(effective_provider, model=effective_model)
    except Exception as exc:
        click.secho(f"Provider error: {exc}", fg="red")
        sys.exit(1)

    ctx = ContextBuilder(report).build()
    advisor = LLMAdvisor(provider=llm_provider)
    hypotheses = advisor.interpret_anomalies(summaries, context=ctx)
    generator = ScenarioGenerator()
    experiments = generator.generate(hypotheses)
    sources = {exp.name: "llm" for exp in experiments}

    ranker = ScenarioRanker()
    ranked = ranker.rank(experiments, sources=sources, top_n=top_n)
    if not ranked:
        click.secho("No scenarios generated.", fg="yellow")
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
@click.option("--top-n", default=5, show_default=True, help="Number of scenarios to return.")
@click.option("--from-catalog", is_flag=True, default=False, help="Use pre-built catalog instead of LLM.")
@click.option(
    "--output", type=click.Choice(["table", "json", "yaml"]),
    default="table", show_default=True,
)
@click.option("--config", "config_path", default=None, help="Path to settings.yaml.")
def generate(provider, model, arch, top_n, from_catalog, output, config_path):
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
        from chaosgen.advisor.llm_advisor import LLMAdvisor, build_provider
        from chaosgen.advisor.scenario_generator import ScenarioGenerator
        from chaosgen.ingestion.analysis import analyze_dataset
        from chaosgen.ingestion.telemetry_factory import build_telemetry_collector

        click.echo(f"  Provider: {effective_provider}")

        try:
            llm_provider = build_provider(effective_provider, model=effective_model)
        except Exception as exc:
            click.secho(f"Provider error: {exc}", fg="red")
            sys.exit(1)

        ctx = ContextBuilder(report).build()
        advisor = LLMAdvisor(provider=llm_provider)

        try:
            collector = build_telemetry_collector(settings)
            dataset = collector.collect_baseline(duration_hours=24)
            _, anomaly_summaries, _ = analyze_dataset(dataset)
        except Exception as exc:
            click.secho(f"  Telemetry unavailable ({exc}), generating from context only.", fg="yellow")
            anomaly_summaries = []

        hypotheses = advisor.interpret_anomalies(anomaly_summaries, context=ctx)
        generator = ScenarioGenerator()
        experiments = generator.generate(hypotheses)
        sources = {exp.name: "llm" for exp in experiments}

    ranker = ScenarioRanker()
    ranked = ranker.rank(experiments, sources=sources, top_n=top_n)

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

    # Architecture
    arch_choices = [t.value for t in ArchitectureType if t != ArchitectureType.UNKNOWN]
    arch_val = click.prompt(
        "Architecture type",
        type=click.Choice(["auto"] + arch_choices),
        default="auto",
    )
    arch = ArchitectureType(arch_val) if arch_val != "auto" else None

    # Environment
    env_choices = [t.value for t in EnvironmentType if t != EnvironmentType.UNKNOWN]
    env_val = click.prompt(
        "Environment type",
        type=click.Choice(["auto"] + env_choices),
        default="auto",
    )
    env = EnvironmentType(env_val) if env_val != "auto" else None

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
        ),
        llm_provider=llm,
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
