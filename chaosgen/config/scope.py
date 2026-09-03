"""
Project scope guards.

Form-first profile mode: DISCOVERY_ENABLED stays False; architecture/environment
come from settings form via profile_presets (WS-1). Heuristic auto-discovery deferred.
"""

from __future__ import annotations

from chaosgen.schemas.discovery import (
    ArchitectureType,
    DiscoveryReport,
    EnvironmentType,
)

# Toggle full hybrid discovery (environment/architecture auto-probe).
DISCOVERY_ENABLED: bool = False

# Current research & implementation focus.
FOCUSED_ARCHITECTURE: ArchitectureType = ArchitectureType.MICROSERVICES
FOCUSED_ENVIRONMENT: EnvironmentType = EnvironmentType.KUBERNETES

_SCOPE_NOTICE = (
    "Profile mode: architecture/environment from settings form when configured; "
    "otherwise default microservices profile. "
    "Heuristic auto-discovery remains disabled (DISCOVERY_ENABLED=False)."
)


def build_focused_discovery_report(
    metrics_endpoint: str | None = None,
    logs_endpoint: str | None = None,
    traces_endpoint: str | None = None,
) -> DiscoveryReport:
    """
    Return the default microservices DiscoveryReport (legacy entry point).

    Delegates to profile_presets; avoids heuristic probing.
    """
    from chaosgen.config.profile_presets import build_preset_discovery_report

    return build_preset_discovery_report(
        FOCUSED_ARCHITECTURE,
        FOCUSED_ENVIRONMENT,
        metrics_endpoint=metrics_endpoint,
        logs_endpoint=logs_endpoint,
        traces_endpoint=traces_endpoint,
        source_message=_SCOPE_NOTICE,
    )


def scope_notice() -> str:
    return _SCOPE_NOTICE
