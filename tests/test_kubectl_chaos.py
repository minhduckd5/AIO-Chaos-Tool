"""Tests for kubectl-chaos module, UCAL k8s mapping, and self-expiring manifests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from chaosgen.advisor.manifest_writer import ManifestWriter
from chaosgen.config.settings import InjectSettings
from chaosgen.modules.kubectl_chaos import KubectlChaosModule
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    NetworkFaultSpec,
    ProcessFaultSpec,
    TargetSpec,
    TargetType,
)
from chaosgen.ucal.translator import ChaosTranslator, ExecutionEnvironment


def _experiment(*faults) -> ChaosExperiment:
    return ChaosExperiment(
        name="checkout-chaos",
        description="test",
        target=TargetSpec(
            type=TargetType.SERVICE,
            name="checkout",
            namespace="default",
            selector={"app": "checkout"},
        ),
        faults=list(faults),
    )


def test_manifest_requires_duration_when_self_expiring():
    writer = ManifestWriter(output_dir=str(Path("scratch") / "test-manifests"))
    fault = ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="")
    with pytest.raises(ValueError, match="duration"):
        writer.write_chaosmesh_fault(
            _experiment(fault),
            fault,
            prefer_self_expiring=True,
        )


def test_manifest_has_managed_by_labels_and_duration(tmp_path):
    writer = ManifestWriter(output_dir=str(tmp_path))
    fault = ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="45s")
    path = writer.write_chaosmesh_fault(
        _experiment(fault),
        fault,
        run_id="run-1",
        managed_by="chaosgen",
        ephemeral="true",
    )
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    labels = doc["metadata"]["labels"]
    assert labels["app.kubernetes.io/managed-by"] == "chaosgen"
    assert labels["chaosgen.io/ephemeral"] == "true"
    assert doc["spec"]["duration"] == "45s"


def test_translator_multi_fault_k8s():
    inject = InjectSettings(enabled=True, kubeconfig="~/.kube/config", chaos_backend="chaosmesh")
    tr = ChaosTranslator(
        forced_env=ExecutionEnvironment.KUBERNETES,
        inject_settings=inject,
    )
    exp = _experiment(
        ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="30s"),
        NetworkFaultSpec(
            fault_type=FaultType.NETWORK_LATENCY,
            duration="30s",
            latency="100ms",
        ),
    )
    plans = tr.translate(exp)
    assert len(plans) == 2
    assert all(p.tool_name == "kubectl-chaos" for p in plans)
    assert plans[0].action == "apply_manifest"
    assert plans[1].action == "apply_manifest"


def test_translator_delete_pod_backend():
    inject = InjectSettings(enabled=True, chaos_backend="delete_pod")
    tr = ChaosTranslator(
        forced_env=ExecutionEnvironment.KUBERNETES,
        inject_settings=inject,
    )
    plans = tr.translate(
        _experiment(ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="30s"))
    )
    assert plans[0].action == "delete_pod"


def test_kubectl_timeout_returns_error():
    mod = KubectlChaosModule(
        {"dry_run": False, "kubectl_timeout_s": 1, "client": "kubectl"}
    )
    with patch("chaosgen.modules.kubectl_chaos.subprocess.run") as run:
        import subprocess

        run.side_effect = subprocess.TimeoutExpired(cmd=["kubectl"], timeout=1)
        result = mod.execute("test_connection", {})
    assert result["success"] is False
    assert result.get("timeout") is True


def test_kubectl_dry_run_skips_mutation():
    mod = KubectlChaosModule({"dry_run": True, "client": "kubectl"})
    result = mod.execute("apply_manifest", {"manifest_path": __file__})
    assert result["success"] is True
    assert result["dry_run"] is True


def test_gc_ephemeral_label_selector():
    mod = KubectlChaosModule(
        {"dry_run": True, "managed_by_label": "chaosgen", "client": "kubectl"}
    )
    result = mod.execute("gc_ephemeral", {})
    assert result["success"] is True
    cmd = " ".join(result["cmd"])
    assert "app.kubernetes.io/managed-by=chaosgen" in cmd
    assert "chaosgen.io/ephemeral=true" in cmd
