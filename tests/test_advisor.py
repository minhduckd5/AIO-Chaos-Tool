"""Tests for the Advisor layer: ScenarioGenerator, ManifestWriter."""
import pytest
from pathlib import Path

from chaosgen.schemas.faults import (
    FaultType, TargetType, TargetSpec, ChaosExperiment,
    NetworkFaultSpec, ProcessFaultSpec,
)
from chaosgen.schemas.scenarios import FaultHypothesis, ScenarioComplexityIndex
from chaosgen.advisor.scenario_generator import ScenarioGenerator
from chaosgen.advisor.manifest_writer import ManifestWriter
from chaosgen.safety.governance import SafetyPolicy


class TestScenarioGenerator:
    def _make_hypothesis(self, confidence=0.8, fault_type=FaultType.NETWORK_LATENCY):
        return FaultHypothesis(
            fault_type=fault_type,
            target_hint="api-gateway",
            rationale="High latency observed",
            confidence=confidence,
            source_cluster_id=0,
            suggested_duration="30s",
            suggested_parameters={"latency": "200ms", "jitter": "20ms"},
        )

    def test_generate_from_hypothesis(self):
        gen = ScenarioGenerator()
        hypotheses = [self._make_hypothesis(confidence=0.9)]
        experiments = gen.generate(hypotheses)
        assert len(experiments) == 1
        assert experiments[0].name.startswith("ai-")
        assert experiments[0].target.name == "api-gateway"

    def test_confidence_threshold_filtering(self):
        gen = ScenarioGenerator(confidence_threshold=0.7)
        hypotheses = [
            self._make_hypothesis(confidence=0.9),
            self._make_hypothesis(confidence=0.5),
            self._make_hypothesis(confidence=0.3),
        ]
        experiments = gen.generate(hypotheses)
        assert len(experiments) == 1

    def test_safety_policy_blocks_protected_namespace(self):
        policy = SafetyPolicy(blocked_namespaces=["kube-system", "monitoring"])
        gen = ScenarioGenerator(safety_policy=policy)
        hyp = FaultHypothesis(
            fault_type=FaultType.PROCESS_KILL,
            target_hint="coredns",
            rationale="test",
            confidence=0.9,
            source_cluster_id=0,
        )
        targets = {"coredns": TargetSpec(
            type=TargetType.POD, name="coredns", namespace="kube-system"
        )}
        experiments = gen.generate([hyp], targets)
        assert len(experiments) == 0

    def test_process_kill_mapping(self):
        gen = ScenarioGenerator()
        hyp = self._make_hypothesis(fault_type=FaultType.PROCESS_KILL)
        experiments = gen.generate([hyp])
        assert len(experiments) == 1
        fault = experiments[0].faults[0]
        assert isinstance(fault, ProcessFaultSpec)

    def test_network_latency_mapping(self):
        gen = ScenarioGenerator()
        hyp = self._make_hypothesis(fault_type=FaultType.NETWORK_LATENCY)
        experiments = gen.generate([hyp])
        fault = experiments[0].faults[0]
        assert isinstance(fault, NetworkFaultSpec)
        assert fault.latency == "200ms"

    def test_compute_sci(self):
        exp = ChaosExperiment(
            name="test",
            target=TargetSpec(type=TargetType.SERVICE, name="x"),
            faults=[
                NetworkFaultSpec(fault_type=FaultType.NETWORK_LATENCY, duration="30s"),
                ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="10s"),
            ],
        )
        sci = ScenarioGenerator.compute_sci(exp)
        score = sci.compute()
        assert score > 0
        assert sci.fault_cardinality == 2


class TestManifestWriter:
    def test_write_litmus_pod_delete(self, tmp_path):
        writer = ManifestWriter(output_dir=str(tmp_path))
        exp = ChaosExperiment(
            name="test-pod-kill",
            target=TargetSpec(type=TargetType.POD, name="frontend", namespace="default"),
            faults=[ProcessFaultSpec(
                fault_type=FaultType.PROCESS_KILL, duration="30s", signal="SIGKILL"
            )],
        )
        path = writer.write_litmus(exp)
        assert path.exists()
        content = path.read_text()
        assert "pod-delete" in content
        assert "frontend" in content

    def test_write_litmus_network_chaos(self, tmp_path):
        writer = ManifestWriter(output_dir=str(tmp_path))
        exp = ChaosExperiment(
            name="test-net-latency",
            target=TargetSpec(type=TargetType.SERVICE, name="api-gw", namespace="default"),
            faults=[NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY, duration="60s",
                latency="200ms", jitter="20ms",
            )],
        )
        path = writer.write_litmus(exp)
        assert path.exists()
        content = path.read_text()
        assert "pod-network-latency" in content
        assert "200" in content

    def test_write_chaosmesh_pod_kill(self, tmp_path):
        writer = ManifestWriter(output_dir=str(tmp_path))
        exp = ChaosExperiment(
            name="cm-pod-kill",
            target=TargetSpec(type=TargetType.POD, name="worker", namespace="prod"),
            faults=[ProcessFaultSpec(
                fault_type=FaultType.PROCESS_KILL, duration="15s",
            )],
        )
        path = writer.write_chaosmesh(exp)
        assert path.exists()
        content = path.read_text()
        assert "PodChaos" in content
        assert "worker" in content
        assert "prod" in content

    def test_write_chaosmesh_network(self, tmp_path):
        writer = ManifestWriter(output_dir=str(tmp_path))
        exp = ChaosExperiment(
            name="cm-net-delay",
            target=TargetSpec(type=TargetType.SERVICE, name="orders"),
            faults=[NetworkFaultSpec(
                fault_type=FaultType.NETWORK_LATENCY, duration="45s",
                latency="150ms", jitter="10ms",
            )],
        )
        path = writer.write_chaosmesh(exp)
        assert path.exists()
        content = path.read_text()
        assert "NetworkChaos" in content
        assert "delay" in content

    def test_no_faults_raises(self, tmp_path):
        writer = ManifestWriter(output_dir=str(tmp_path))
        exp = ChaosExperiment(
            name="empty",
            target=TargetSpec(type=TargetType.SERVICE, name="x"),
            faults=[],
        )
        with pytest.raises(ValueError):
            writer.write_litmus(exp)
