"""
Connect profile routing — map form-first settings to UCAL env and module configs (WS-3).
"""

from __future__ import annotations

import logging
from typing import Any

from chaosgen.config.profile_presets import default_environment_for
from chaosgen.config.settings import ChaosGenSettings
from chaosgen.schemas.discovery import ArchitectureType, EnvironmentType
from chaosgen.ucal.translator import ExecutionEnvironment

logger = logging.getLogger("ChaosOrchestrator")


def resolve_effective_environment(settings: ChaosGenSettings) -> EnvironmentType:
    hints = settings.hints
    if hints.environment is not None:
        return hints.environment
    if hints.architecture is not None:
        return default_environment_for(hints.architecture)
    return EnvironmentType.KUBERNETES


def execution_environment_from_settings(
    settings: ChaosGenSettings,
) -> ExecutionEnvironment | None:
    """Map operator environment hint to UCAL execution environment."""
    env = resolve_effective_environment(settings)
    mapping = {
        EnvironmentType.KUBERNETES: ExecutionEnvironment.KUBERNETES,
        EnvironmentType.DOCKER_COMPOSE: ExecutionEnvironment.DOCKER,
        EnvironmentType.BARE_METAL: ExecutionEnvironment.SYSTEMD,
        EnvironmentType.CLOUD_VM: ExecutionEnvironment.SYSTEMD,
    }
    return mapping.get(env)


def sync_inject_from_connect(settings: ChaosGenSettings) -> None:
    """Keep legacy inject.* paths aligned with connect.kubernetes (boutique compat)."""
    k = settings.connect.kubernetes
    if k.kubeconfig:
        settings.inject.kubeconfig = k.kubeconfig
    if k.context:
        settings.inject.context = k.context
    if k.default_namespace:
        settings.inject.default_namespace = k.default_namespace
    if k.kubeconfig or k.context:
        settings.inject.enabled = True


def module_connect_configs(settings: ChaosGenSettings) -> dict[str, dict[str, Any]]:
    """Per-module config fragments derived from connect block."""
    inj = settings.inject
    bastion = settings.connect.kubernetes.ssh_bastion
    k8s_cfg: dict[str, Any] = {
        "kubeconfig": settings.connect.kubernetes.kubeconfig or inj.kubeconfig,
        "context": settings.connect.kubernetes.context or inj.context,
        "default_namespace": settings.connect.kubernetes.default_namespace or inj.default_namespace,
        "dry_run": inj.dry_run,
        "client": inj.client,
        "kubectl_timeout_s": inj.kubectl_timeout_s,
        "delete_force_on_timeout": inj.delete_force_on_timeout,
        "managed_by_label": inj.managed_by_label,
        "ephemeral_label": inj.ephemeral_label,
        "ssh_bastion": bastion.model_dump() if bastion else {},
    }

    docker = settings.connect.docker
    pumba_cfg: dict[str, Any] = {
        "docker_host": docker.host,
        "compose_file": docker.compose_file,
        "project_name": docker.project_name,
        "dry_run": inj.dry_run,
    }

    tox_cfg: dict[str, Any] = {
        "api_url": settings.connect.toxiproxy.api_url,
        "dry_run": inj.dry_run,
    }

    broker = settings.connect.broker
    broker_cfg: dict[str, Any] = {
        "type": broker.type,
        "bootstrap": broker.bootstrap,
        "admin_api_url": broker.admin_api_url,
        "dry_run": inj.dry_run,
    }

    return {
        "kubectl-chaos": k8s_cfg,
        "pumba": pumba_cfg,
        "toxiproxy": tox_cfg,
        "broker": broker_cfg,
    }


def architecture_from_settings(settings: ChaosGenSettings) -> ArchitectureType:
    return settings.hints.architecture or ArchitectureType.MICROSERVICES


def apply_connect_profile_to_orchestrator(orchestrator: Any) -> None:
    """
    Apply connect + hints to an orchestrator instance (translator + module configs).

    Called at init and after settings reload.
    """
    settings: ChaosGenSettings | None = getattr(orchestrator, "_cg_settings", None)
    if settings is None:
        return

    sync_inject_from_connect(settings)
    exec_env = execution_environment_from_settings(settings)
    if exec_env is not None:
        orchestrator.translator.set_environment(exec_env)
        orchestrator.translator.inject = settings.inject
        orchestrator.translator.hints_environment = resolve_effective_environment(settings).value

    connect_cfgs = module_connect_configs(settings)
    for module_name, cfg in connect_cfgs.items():
        mod = orchestrator.modules.get(module_name)
        if mod is None:
            continue
        merged = {**getattr(mod, "config", {}), **{k: v for k, v in cfg.items() if v is not None}}
        mod.config = merged

    arch = architecture_from_settings(settings)
    tier = "P0" if arch in {
        ArchitectureType.MICROSERVICES,
        ArchitectureType.MODULAR_MONOLITH,
    } else "P1"
    logger.info(
        "Connect profile applied: arch=%s env=%s tier=%s",
        arch.value,
        resolve_effective_environment(settings).value,
        tier,
    )
