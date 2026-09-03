"""Map Experiments form rows to fault-centric CTK intent (no Qt)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from chaosgen.schemas.chaos_intent import (
    CtkExperimentIntent,
    CtkFaultIntent,
    CtkTargetRef,
)
from chaosgen.schemas.ctk_experiment import CtkActivity, CtkExperiment
from chaosgen.ucal.ctk_builder import build_experiment_from_intent

# Environments that must NOT emit chaosk8s / namespace selectors.
_P1_ENVS = frozenset({"bare_metal", "cloud_vm", "serverless"})
_DOCKER_ENVS = frozenset({"docker_compose", "docker"})


def build_experiment_intent(
    *,
    title: str,
    description: str,
    fault_rows: List[Dict[str, Any]],
    default_namespace: str = "default",
    default_label_key: str = "app",
    action_pause_seconds: float = 0,
    include_steady_state: bool = False,
    prom_url: Optional[str] = None,
    auto_rollback: bool = True,
    max_actions: int = 3,
    blocked_namespaces: Optional[List[str]] = None,
    kube_context: Optional[str] = None,
) -> CtkExperimentIntent:
    """
  Each fault row dict:
    ftype, duration, latency, loss_percentage, signal, services (list[str])
    """
    faults: List[CtkFaultIntent] = []
    ns = (default_namespace or "default").strip()
    label_key = (default_label_key or "app").strip()

    for row in fault_rows:
        services = [s.strip() for s in row.get("services") or [] if str(s).strip()]
        if not services:
            continue
        faults.append(
            CtkFaultIntent(
                fault_type=str(row.get("ftype") or "process_kill"),
                duration=str(row.get("duration") or "30s"),
                latency=str(row.get("latency") or "100ms"),
                loss_percentage=float(row.get("loss_percentage") or 10.0),
                signal=str(row.get("signal") or "SIGKILL"),
                targets=[
                    CtkTargetRef(service=svc, namespace=ns, label_key=label_key)
                    for svc in services
                ],
            )
        )

    if not faults:
        raise ValueError("Add at least one fault with selected targets")

    return CtkExperimentIntent(
        title=title,
        description=description or title,
        faults=faults,
        action_pause_seconds=float(action_pause_seconds or 0),
        include_steady_state=include_steady_state,
        prom_url=prom_url,
        auto_rollback=auto_rollback,
        max_actions=max_actions,
        blocked_namespaces=blocked_namespaces or ["kube-system", "monitoring"],
        kube_context=kube_context,
    )


def intent_to_ctk_experiment(intent: CtkExperimentIntent) -> CtkExperiment:
    return build_experiment_from_intent(intent)


def intent_to_json(intent: CtkExperimentIntent, indent: int = 2) -> str:
    import json

    exp = intent_to_ctk_experiment(intent)
    return json.dumps(exp.to_ctk_dict(), indent=indent)


def _first_fault_meta(fault_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not fault_rows:
        return {
            "ftype": "process_kill",
            "duration": "30s",
            "latency": "100ms",
            "signal": "SIGKILL",
            "loss_percentage": 10.0,
        }
    row = fault_rows[0]
    return {
        "ftype": str(row.get("ftype") or "process_kill"),
        "duration": str(row.get("duration") or "30s"),
        "latency": str(row.get("latency") or "100ms"),
        "signal": str(row.get("signal") or "SIGKILL"),
        "loss_percentage": float(row.get("loss_percentage") or 10.0),
    }


def _normalize_targets(
    *,
    environment: str,
    fault_rows: List[Dict[str, Any]],
    host_target: Optional[str] = None,
    faas_function: Optional[str] = None,
) -> List[str]:
    """Resolve display targets: prefer connect-panel fields for P1."""
    env = (environment or "").strip().lower()
    if env in ("bare_metal", "cloud_vm") and host_target and host_target.strip():
        return [host_target.strip()]
    if env == "serverless" and faas_function and faas_function.strip():
        return [faas_function.strip()]

    targets: List[str] = []
    for row in fault_rows:
        for svc in row.get("services") or []:
            name = str(svc).strip()
            if name and name not in targets:
                targets.append(name)
    if not targets:
        raise ValueError(
            "Select at least one target (fault-row service, Host IP, or Function ARN)"
        )
    return targets


def _host_process_action(
    *,
    target: str,
    ftype: str,
    duration: str,
    signal: str,
) -> CtkActivity:
    """Bare Metal / Cloud VM → CTK process provider (no chaosk8s)."""
    ft = (ftype or "").lower()
    if ft in ("resource_exhaustion", "cpu", "stress"):
        return CtkActivity(
            type="action",
            name=f"stress-host-{target}",
            provider={
                "type": "process",
                "path": "stress-ng",
                "arguments": ["--cpu", "1", "--timeout", duration],
                "timeout": 120,
            },
        )
    # Default: process kill via shell (SSH target encoded in name/tags)
    sig = (signal or "SIGKILL").replace("SIG", "")
    return CtkActivity(
        type="action",
        name=f"kill-host-{target}",
        provider={
            "type": "process",
            "path": "kill",
            "arguments": [f"-{sig}", "1"],
            "timeout": 30,
        },
    )


def _serverless_http_action(
    *,
    function_ref: str,
    ftype: str,
    latency: str,
) -> CtkActivity:
    """Serverless → boundary fault via HTTP provider (no FaaS compute inject)."""
    # Treat function ARN/name as dependency boundary URL when not already a URL.
    url = function_ref
    if not url.startswith(("http://", "https://")):
        url = f"https://boundary.local/invoke/{function_ref.lstrip('/')}"

    ft = (ftype or "").lower()
    timeout_s = 1.0
    if "latency" in ft or "timeout" in ft or "network" in ft:
        # Aggressive timeout to emulate dependency SLA breach in dry-run docs
        timeout_s = 0.05

    return CtkActivity(
        type="action",
        name=f"boundary-fault-{function_ref.split('/')[-1][:48] or 'fn'}",
        provider={
            "type": "http",
            "url": url,
            "method": "GET",
            "timeout": timeout_s,
            "headers": {
                "X-ChaosGen-Mode": "p1-dryrun-boundary",
                "X-ChaosGen-Inject": ftype or "boundary_latency",
                "X-ChaosGen-Latency-Hint": latency,
            },
        },
    )


def _docker_process_action(
    *,
    target: str,
    ftype: str,
    duration: str,
    signal: str,
    latency: str,
    loss_percentage: float,
) -> CtkActivity:
    """Docker Compose → process provider wrapping Pumba / docker CLI."""
    ft = (ftype or "").lower()
    if ft in ("network_latency", "packet_loss"):
        args = ["netem", "--duration", duration, "delay", latency, target]
        if ft == "packet_loss":
            args = [
                "netem",
                "--duration",
                duration,
                "loss",
                f"{int(loss_percentage)}%",
                target,
            ]
        return CtkActivity(
            type="action",
            name=f"pumba-netem-{target}",
            provider={
                "type": "process",
                "path": "pumba",
                "arguments": args,
                "timeout": 120,
            },
        )
    sig = signal or "SIGKILL"
    return CtkActivity(
        type="action",
        name=f"pumba-kill-{target}",
        provider={
            "type": "process",
            "path": "pumba",
            "arguments": ["kill", "--signal", sig, target],
            "timeout": 60,
        },
    )


def build_ctk_experiment_for_environment(
    *,
    environment: str,
    title: str,
    description: str,
    fault_rows: List[Dict[str, Any]],
    default_namespace: str = "default",
    default_label_key: str = "app",
    action_pause_seconds: float = 0,
    auto_rollback: bool = True,
    max_actions: int = 3,
    host_target: Optional[str] = None,
    host_ssh_user: Optional[str] = None,
    host_ssh_port: Optional[int] = None,
    faas_provider: Optional[str] = None,
    faas_function: Optional[str] = None,
    kube_context: Optional[str] = None,
) -> CtkExperiment:
    """
    Build a CTK experiment document matched to the selected environment.

    kubernetes → chaosk8s (existing builder)
    docker_compose → process / pumba
    bare_metal | cloud_vm → process (stress-ng / kill)
    serverless → http boundary fault
    """
    env = (environment or "kubernetes").strip().lower()
    meta = _first_fault_meta(fault_rows)

    if env == "kubernetes":
        intent = build_experiment_intent(
            title=title,
            description=description,
            fault_rows=fault_rows,
            default_namespace=default_namespace,
            default_label_key=default_label_key,
            action_pause_seconds=action_pause_seconds,
            auto_rollback=auto_rollback,
            max_actions=max_actions,
            kube_context=kube_context,
        )
        return intent_to_ctk_experiment(intent)

    targets = _normalize_targets(
        environment=env,
        fault_rows=fault_rows,
        host_target=host_target,
        faas_function=faas_function,
    )[: max(1, int(max_actions))]

    method: List[CtkActivity] = []
    tags = ["chaosgen", f"env:{env}", "dry-run"]

    if env in _DOCKER_ENVS:
        tags.append("provider:pumba")
        for t in targets:
            method.append(
                _docker_process_action(
                    target=t,
                    ftype=meta["ftype"],
                    duration=meta["duration"],
                    signal=meta["signal"],
                    latency=meta["latency"],
                    loss_percentage=meta["loss_percentage"],
                )
            )
    elif env in ("bare_metal", "cloud_vm"):
        tags.append("provider:process")
        if host_ssh_user:
            tags.append(f"ssh_user:{host_ssh_user}")
        if host_ssh_port:
            tags.append(f"ssh_port:{host_ssh_port}")
        for t in targets:
            method.append(
                _host_process_action(
                    target=t,
                    ftype=meta["ftype"],
                    duration=meta["duration"],
                    signal=meta["signal"],
                )
            )
    elif env == "serverless":
        tags.append("provider:http-boundary")
        if faas_provider:
            tags.append(f"faas:{faas_provider}")
        for t in targets:
            method.append(
                _serverless_http_action(
                    function_ref=t,
                    ftype=meta["ftype"],
                    latency=meta["latency"],
                )
            )
    else:
        raise ValueError(f"Unsupported environment for CTK preview: {env!r}")

    pause = float(action_pause_seconds or 0)
    if pause > 0 and method:
        method[0].pauses = {"after": pause}

    return CtkExperiment(
        title=title or "ChaosGen experiment",
        description=description or title or "ChaosGen experiment",
        method=method,
        tags=tags,
        contributions={
            "reliability": "high",
            "security": "none",
            "environment": env,
            "dry_run": True if env in _P1_ENVS or env in _DOCKER_ENVS else False,
        },
    )


def experiment_to_json(experiment: CtkExperiment, indent: int = 2) -> str:
    import json

    return json.dumps(experiment.to_ctk_dict(), indent=indent)
