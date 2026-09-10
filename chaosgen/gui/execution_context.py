"""Honest execution-context summary for Telemetry Approve (operator machine)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def format_execution_context(settings: Any | None = None) -> str:
    """
    Describe what Approve will actually use — never invent a green default.

    If kubeconfig/context are missing, say so explicitly.
    """
    if settings is None:
        from chaosgen.config.settings import load_settings

        settings = load_settings()

    inj = getattr(settings, "inject", None)
    conn = getattr(settings, "connect", None)
    kube_cfg = None
    context = None
    if conn is not None and getattr(conn, "kubernetes", None) is not None:
        kube_cfg = conn.kubernetes.kubeconfig or None
        context = conn.kubernetes.context or None
    if inj is not None:
        kube_cfg = kube_cfg or inj.kubeconfig or None
        context = context or inj.context or None

    namespace = getattr(inj, "default_namespace", None) if inj is not None else None
    namespace = (namespace or "").strip() or None

    kube_path = Path(kube_cfg).expanduser() if kube_cfg else None
    kube_exists = bool(kube_path and kube_path.is_file())

    from chaosgen.config.telemetry_endpoints import resolve_prometheus_url

    try:
        prom_url = resolve_prometheus_url(settings)
    except Exception:
        prom_url = None

    if not kube_exists and not context:
        env_line = (
            "Execution environment not determined "
            "(no kubeconfig file / context in settings)."
        )
    else:
        parts = []
        if kube_exists:
            parts.append(f"kubeconfig={kube_path}")
        elif kube_cfg:
            parts.append(f"kubeconfig={kube_cfg} (missing on disk)")
        else:
            parts.append("kubeconfig=unset")
        parts.append(f"context={context or 'unset'}")
        parts.append(f"namespace={namespace or 'unset'}")
        env_line = "Inject context: " + " · ".join(parts)

    ss_line = (
        f"Steady-state: Prom {prom_url} (operator-side; "
        'default query up{job=~".+"} passes if any series returns — '
        "not a target-health guarantee)."
        if prom_url
        else "Steady-state: Prometheus URL not determined."
    )
    return f"{env_line}\n{ss_line}"
