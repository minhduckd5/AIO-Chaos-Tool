#!/usr/bin/env python3
"""
Multi-architecture profile matrix — thesis rehearsal (WS-6).

Loops all six architecture profiles through form-first resolution, connect
validation, catalog listing, and inject routing — without live chaos unless
--live-inject is passed (P0 only: microservices + modular_monolith).

Usage:
  python scripts/demo_multi_arch_matrix.py
  python scripts/demo_multi_arch_matrix.py --output docs/multi-arch-matrix-report.json
  python scripts/demo_multi_arch_matrix.py --markdown docs/multi-arch-matrix.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chaosgen.advisor.scenario_catalog import ScenarioCatalog
from chaosgen.config.connect_routing import (
    execution_environment_from_settings,
    module_connect_configs,
)
from chaosgen.config.profile_presets import P0_LIVE_ARCHITECTURES, profile_priority_tier
from chaosgen.config.profile_validation import validate_profile_connect
from chaosgen.config.settings import (
    BrokerConnectSettings,
    ChaosGenSettings,
    ConnectSettings,
    DockerConnectSettings,
    InjectSettings,
    KubernetesConnectSettings,
    ToxiproxyConnectSettings,
    UserHints,
)
from chaosgen.discovery import resolve_discovery_report
from chaosgen.modules.pumba import PumbaModule
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType

DEFAULT_OUT = ROOT / "docs" / "multi-arch-matrix-report.json"

# Example settings files when present (optional enrichment)
EXAMPLE_SETTINGS: dict[ArchitectureType, str] = {
    ArchitectureType.MICROSERVICES: "examples/registry-vm-settings.yaml",
    ArchitectureType.MODULAR_MONOLITH: "examples/modular-monolith-settings.yaml",
}

MATRIX_ARCHITECTURES = [
    ArchitectureType.MICROSERVICES,
    ArchitectureType.MODULAR_MONOLITH,
    ArchitectureType.EVENT_DRIVEN,
    ArchitectureType.MONOLITH,
    ArchitectureType.CLIENT_SERVER,
    ArchitectureType.SERVERLESS,
]


@dataclass
class ProfileRow:
    architecture: str
    environment: str
    tier: str
    connect_valid: bool
    connect_errors: list[str] = field(default_factory=list)
    catalog_entries: int = 0
    catalog_names: list[str] = field(default_factory=list)
    service_nodes: list[str] = field(default_factory=list)
    execution_env: str | None = None
    inject_dry_run: bool = True
    inject_module: str | None = None
    example_settings: str | None = None
    live_inject_attempted: bool = False
    live_inject_result: dict | None = None


def _settings_for_architecture(
    arch: ArchitectureType,
    *,
    dry_run: bool,
    use_examples: bool = False,
) -> ChaosGenSettings:
    """Minimal valid connect block per profile (form-first matrix)."""
    rel_example = EXAMPLE_SETTINGS.get(arch) if use_examples else None
    if rel_example:
        path = ROOT / rel_example
        if path.is_file():
            from chaosgen.config.settings import load_settings

            settings = load_settings(str(path))
            if dry_run:
                settings.inject.dry_run = True
            return settings

    hints = UserHints(architecture=arch, skip_auto_detect=True)
    connect = ConnectSettings()
    inject = InjectSettings(enabled=True, dry_run=dry_run)

    if arch == ArchitectureType.MICROSERVICES:
        connect.kubernetes = KubernetesConnectSettings(kubeconfig=str(Path.home() / ".kube" / "config"))
    elif arch == ArchitectureType.MODULAR_MONOLITH:
        connect.docker = DockerConnectSettings(
            host="npipe:////./pipe/docker_engine",
            compose_file="labs/modular-monolith/docker-compose.yml",
            project_name="chaosgen-monolith",
        )
        inject.dry_run = dry_run
    elif arch == ArchitectureType.MONOLITH:
        hints.environment = EnvironmentType.DOCKER_COMPOSE
        connect.docker = DockerConnectSettings(
            host="npipe:////./pipe/docker_engine",
            compose_file="labs/modular-monolith/docker-compose.yml",
        )
    elif arch == ArchitectureType.EVENT_DRIVEN:
        hints.environment = EnvironmentType.DOCKER_COMPOSE
        connect.broker = BrokerConnectSettings(type="redpanda", bootstrap="localhost:9092")
    elif arch == ArchitectureType.CLIENT_SERVER:
        connect.toxiproxy = ToxiproxyConnectSettings(api_url="http://127.0.0.1:8474")
    # serverless: no mandatory connect; dry-run only

    return ChaosGenSettings(hints=hints, connect=connect, inject=inject)


def _inject_module_for(arch: ArchitectureType, settings: ChaosGenSettings) -> str | None:
    env = execution_environment_from_settings(settings)
    if env is None:
        return None
    name = env.value
    if arch == ArchitectureType.MICROSERVICES:
        return "kubectl-chaos"
    if arch == ArchitectureType.MODULAR_MONOLITH:
        return "pumba"
    if arch == ArchitectureType.CLIENT_SERVER:
        return "toxiproxy"
    if arch == ArchitectureType.EVENT_DRIVEN:
        return "broker"
    return name


def _evaluate_profile(
    arch: ArchitectureType,
    *,
    dry_run: bool,
    live_inject: bool,
    use_examples: bool = False,
) -> ProfileRow:
    settings = _settings_for_architecture(arch, dry_run=dry_run, use_examples=use_examples)
    validation = validate_profile_connect(settings)
    report = resolve_discovery_report(settings=settings)
    catalog = ScenarioCatalog()
    entries = catalog.get_all(arch)
    exec_env = execution_environment_from_settings(settings)
    module = _inject_module_for(arch, settings)
    cfgs = module_connect_configs(settings)

    row = ProfileRow(
        architecture=arch.value,
        environment=report.environment.type.value,
        tier=profile_priority_tier(arch),
        connect_valid=validation.ok,
        connect_errors=list(validation.errors),
        catalog_entries=len(entries),
        catalog_names=[e.name for e in entries[:8]],
        service_nodes=[n.id for n in report.service_map.nodes],
        execution_env=exec_env.value if exec_env else None,
        inject_dry_run=settings.inject.dry_run,
        inject_module=module,
        example_settings=EXAMPLE_SETTINGS.get(arch),
    )

    if live_inject and arch in P0_LIVE_ARCHITECTURES and arch == ArchitectureType.MODULAR_MONOLITH:
        pumba_cfg = {**cfgs.get("pumba", {}), "dry_run": False}
        mod = PumbaModule(pumba_cfg)
        row.live_inject_attempted = True
        row.live_inject_result = mod.execute(
            "kill_container",
            {"container": "monolith-app", "signal": "SIGKILL"},
        )

    return row


def _print_table(rows: list[ProfileRow]) -> None:
    print("\n=== ChaosGen Multi-Architecture Matrix ===\n")
    hdr = f"{'Architecture':<18} {'Tier':<4} {'Connect':<8} {'Catalog':<8} {'Exec':<12} {'Module':<14} dry_run"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        ok = "OK" if r.connect_valid else "FAIL"
        print(
            f"{r.architecture:<18} {r.tier:<4} {ok:<8} {r.catalog_entries:<8} "
            f"{(r.execution_env or '-'):<12} {(r.inject_module or '-'):<14} {r.inject_dry_run}"
        )
    print()


def _write_markdown(path: Path, rows: list[ProfileRow], generated_at: str) -> None:
    lines = [
        "# Multi-Architecture Profile Matrix",
        "",
        f"Generated: {generated_at}",
        "",
        "| Architecture | Tier | Connect | Catalog | Exec env | Inject module | dry_run |",
        "|--------------|------|---------|---------|----------|---------------|---------|",
    ]
    for r in rows:
        ok = "OK" if r.connect_valid else "FAIL"
        lines.append(
            f"| {r.architecture} | {r.tier} | {ok} | {r.catalog_entries} | "
            f"{r.execution_env or '-'} | {r.inject_module or '-'} | {r.inject_dry_run} |"
        )
    lines.extend(["", "## Catalog samples", ""])
    for r in rows:
        lines.append(f"### {r.architecture}")
        if r.catalog_names:
            for name in r.catalog_names:
                lines.append(f"- {name}")
        else:
            lines.append("- *(no builtin entries)*")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="WS-6 multi-arch profile matrix dry-run")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--markdown", type=Path, default=None, help="Optional markdown summary path")
    parser.add_argument(
        "--live-inject",
        action="store_true",
        help="P0 modular_monolith only: real Pumba SIGKILL (destructive; lab must be up)",
    )
    parser.add_argument(
        "--p0-live",
        action="store_true",
        help="Set inject.dry_run=false for P0 profiles in matrix metadata (no inject unless --live-inject)",
    )
    parser.add_argument(
        "--use-example-settings",
        action="store_true",
        help="Load examples/*-settings.yaml when available (slower: observability probe)",
    )
    args = parser.parse_args()

    generated_at = datetime.now(timezone.utc).isoformat()
    rows: list[ProfileRow] = []

    for arch in MATRIX_ARCHITECTURES:
        dry_run = not (args.p0_live and arch in P0_LIVE_ARCHITECTURES)
        rows.append(
            _evaluate_profile(
                arch,
                dry_run=dry_run,
                live_inject=args.live_inject,
                use_examples=args.use_example_settings,
            )
        )

    _print_table(rows)

    payload = {
        "generated_at": generated_at,
        "mode": "live_inject" if args.live_inject else "dry_run",
        "profiles": [asdict(r) for r in rows],
        "evidence": {
            "modular_monolith_fail": "docs/modular-monolith-verdict-report.json",
            "modular_monolith_pass_hitl": "docs/modular-monolith-verdict-rerun.json",
            "modular_monolith_bleed": "docs/modular-monolith-live-fire.json",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved: {args.output}")

    if args.markdown:
        _write_markdown(args.markdown, rows, generated_at)
        print(f"Saved: {args.markdown}")

    failed = [r.architecture for r in rows if not r.connect_valid]
    if failed:
        print("Connect validation failed for:", ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
