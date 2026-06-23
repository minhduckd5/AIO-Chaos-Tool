"""Tests for BlastRadiusController (P6 coverage)."""
from __future__ import annotations

import pytest

from chaosgen.safety.governance import BlastRadiusController, SafetyPolicy
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    NetworkFaultSpec,
    TargetSpec,
    TargetType,
)


def _experiment(name: str = "test-exp", namespace: str = "app", target_name: str = "api") -> ChaosExperiment:
    return ChaosExperiment(
        name=name,
        target=TargetSpec(type=TargetType.POD, name=target_name, namespace=namespace),
        faults=[
            NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY,
                duration="30s",
                latency="100ms",
            )
        ],
    )


class TestBlastRadiusController:
    def test_valid_experiment_passes(self):
        ctrl = BlastRadiusController()
        assert ctrl.validate_experiment(_experiment()) is True

    def test_blocked_namespace_raises(self):
        ctrl = BlastRadiusController()
        with pytest.raises(ValueError, match="blocked"):
            ctrl.validate_experiment(_experiment(namespace="kube-system"))

    def test_blocked_service_raises(self):
        policy = SafetyPolicy(blocked_services=["database-master"])
        ctrl = BlastRadiusController(policy)
        with pytest.raises(ValueError, match="protected"):
            ctrl.validate_experiment(_experiment(target_name="database-master"))

    def test_validate_targets_count(self):
        ctrl = BlastRadiusController(SafetyPolicy(max_affected_nodes=1))
        assert ctrl.validate_targets(["pod-a"]) is True
        with pytest.raises(ValueError, match="exceeds limit"):
            ctrl.validate_targets(["pod-a", "pod-b"])
