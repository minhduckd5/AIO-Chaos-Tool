"""
ChaosGen Hybrid Discovery Engine.

Merges user-provided hints (settings.yaml) with auto-detection probes.
Priority: user_override > auto_detected > heuristic_fallback.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from chaosgen.discovery.architecture_classifier import ArchitectureClassifier
from chaosgen.discovery.environment_probe import EnvironmentProbe
from chaosgen.discovery.observability_probe import ObservabilityProbe
from chaosgen.discovery.service_mapper import ServiceMapper
from chaosgen.schemas.discovery import (
    DiscoveryReport,
    DiscoverySignal,
    EnvironmentProfile,
    HintSource,
    ServiceMap,
)

if TYPE_CHECKING:
    from chaosgen.config.settings import ChaosGenSettings

logger = logging.getLogger(__name__)

__all__ = [
    "EnvironmentProbe",
    "ArchitectureClassifier",
    "ServiceMapper",
    "ObservabilityProbe",
    "DiscoveryReport",
    "run_full_discovery",
]


def run_full_discovery(
    settings: "ChaosGenSettings | None" = None,
    kubeconfig: str | None = None,
    compose_file: str | None = None,
) -> DiscoveryReport:
    """
    Execute the full hybrid discovery pipeline.

    If a ChaosGenSettings is provided, user hints are merged with auto-detection:
      - Environment type override
      - Architecture type override (with mismatch signaling)
      - Observability tool URLs + auth
      - skip_auto_detect flag

    Non-fatal errors from individual probes are captured in report.discovery_errors.
    """
    from chaosgen.config.settings import ChaosGenSettings, UserHints

    hints = settings.hints if settings else UserHints()
    errors: list[str] = []
    signals: list[DiscoverySignal] = []

    # --- Environment ---
    if hints.environment is not None:
        env_profile = EnvironmentProfile(type=hints.environment)
        signals.append(DiscoverySignal(
            source=HintSource.USER_OVERRIDE,
            key="environment_type",
            value=hints.environment.value,
            message=f"User-specified environment: {hints.environment.value}",
        ))
    else:
        env_probe = EnvironmentProbe(kubeconfig=kubeconfig)
        env_profile = env_probe.probe()
        signals.append(DiscoverySignal(
            source=HintSource.AUTO_DETECTED,
            key="environment_type",
            value=env_profile.type.value,
            message=f"Auto-detected environment: {env_profile.type.value}",
        ))

    # --- Architecture ---
    if hints.skip_auto_detect and hints.architecture is not None:
        from chaosgen.schemas.discovery import ArchitectureProfile
        arch_profile = ArchitectureProfile(
            type=hints.architecture,
            confidence=1.0,
            signals=[f"User override (skip_auto_detect): {hints.architecture.value}"],
        )
        signals.append(DiscoverySignal(
            source=HintSource.USER_OVERRIDE,
            key="architecture_type",
            value=hints.architecture.value,
            message="Architecture set by user (auto-detect skipped)",
        ))
    else:
        arch_classifier = ArchitectureClassifier(
            env_profile=env_profile,
            compose_file=compose_file,
            kubeconfig=kubeconfig,
        )
        arch_profile = arch_classifier.classify(user_hint=hints.architecture)

        mismatch_signals = [
            s for s in arch_profile.signals if "USER_HEURISTIC_MISMATCH" in s
        ]
        if mismatch_signals:
            signals.append(DiscoverySignal(
                source=HintSource.USER_OVERRIDE,
                key="architecture_type",
                value=hints.architecture.value if hints.architecture else "none",
                flag="USER_HEURISTIC_MISMATCH",
                message=mismatch_signals[0],
            ))
        else:
            source = HintSource.USER_OVERRIDE if hints.architecture else HintSource.AUTO_DETECTED
            signals.append(DiscoverySignal(
                source=source,
                key="architecture_type",
                value=arch_profile.type.value,
                confidence=arch_profile.confidence,
                message=f"Architecture: {arch_profile.type.value} (confidence={arch_profile.confidence:.2f})",
            ))

    # --- Service Map ---
    try:
        service_mapper = ServiceMapper(
            env_profile=env_profile,
            compose_file=compose_file,
            kubeconfig=kubeconfig,
        )
        service_map = service_mapper.build()
    except Exception as exc:
        errors.append(f"ServiceMapper failed: {exc}")
        service_map = ServiceMap()

    # --- Observability ---
    obs_probe = ObservabilityProbe(
        hints=hints.observability if hints.observability else None,
    )
    obs_profile, obs_signals = obs_probe.probe()
    signals.extend(obs_signals)

    return DiscoveryReport(
        environment=env_profile,
        architecture=arch_profile,
        service_map=service_map,
        observability=obs_profile,
        signals=signals,
        discovery_errors=errors,
    )
