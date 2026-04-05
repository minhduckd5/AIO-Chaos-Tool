"""
Tests for ContextBuilder and ScenarioContext.
"""

from __future__ import annotations

from chaosgen.advisor.context_builder import ContextBuilder, ScenarioContext
from chaosgen.schemas.discovery import (
    ArchitectureProfile,
    ArchitectureType,
    DiscoveryReport,
    EnvironmentProfile,
    EnvironmentType,
    ObservabilityProfile,
    ServiceMap,
)


def _make_report(
    arch_type: ArchitectureType = ArchitectureType.MICROSERVICES,
    env_type: EnvironmentType = EnvironmentType.KUBERNETES,
) -> DiscoveryReport:
    return DiscoveryReport(
        environment=EnvironmentProfile(type=env_type),
        architecture=ArchitectureProfile(type=arch_type, service_count=5, confidence=0.75),
        service_map=ServiceMap(),
        observability=ObservabilityProfile(has_metrics=True, has_logs=True),
    )


class TestContextBuilder:
    def test_returns_scenario_context(self):
        report = _make_report()
        ctx = ContextBuilder(report).build()
        assert isinstance(ctx, ScenarioContext)

    def test_architecture_type_propagated(self):
        report = _make_report(arch_type=ArchitectureType.EVENT_DRIVEN)
        ctx = ContextBuilder(report).build()
        assert ctx.architecture_type == ArchitectureType.EVENT_DRIVEN

    def test_environment_propagated(self):
        report = _make_report(env_type=EnvironmentType.DOCKER_COMPOSE)
        ctx = ContextBuilder(report).build()
        assert ctx.environment.type == EnvironmentType.DOCKER_COMPOSE

    def test_previous_experiments_capped(self):
        from chaosgen.schemas.faults import ChaosExperiment, TargetSpec, TargetType, NetworkFaultSpec, FaultType
        report = _make_report()
        exps = []
        for i in range(10):
            exps.append(ChaosExperiment(
                name=f"exp-{i}",
                target=TargetSpec(type=TargetType.SERVICE, name="svc"),
                faults=[NetworkFaultSpec(fault_type=FaultType.NETWORK_LATENCY, duration="30s", latency="100ms")],
            ))
        ctx = ContextBuilder(report).build(previous_experiments=exps)
        assert len(ctx.previous_experiment_names) <= 5

    def test_prompt_text_contains_architecture(self):
        report = _make_report(arch_type=ArchitectureType.MONOLITH)
        ctx = ContextBuilder(report).build()
        prompt = ctx.to_prompt_text()
        assert "monolith" in prompt.lower()

    def test_prompt_text_is_non_empty_string(self):
        report = _make_report()
        ctx = ContextBuilder(report).build()
        prompt = ctx.to_prompt_text()
        assert isinstance(prompt, str)
        assert len(prompt) > 50
