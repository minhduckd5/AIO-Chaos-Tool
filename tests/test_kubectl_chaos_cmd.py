"""kubectl fallback backend: command construction, dry-run gate, rollback fallbacks.

`client="kubectl"` pins the subprocess path; the native client is covered
separately. Nothing here touches a cluster.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from chaosgen.modules import kubectl_chaos as kc
from chaosgen.modules.kubectl_chaos import (
    CHAOS_KINDS,
    EPHEMERAL_LABEL,
    MANAGED_BY_LABEL,
    KubectlChaosModule,
)


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def recorder(monkeypatch):
    """Capture every kubectl argv; queue of canned results."""
    calls: list[list[str]] = []
    responses: list[_Completed] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return responses.pop(0) if responses else _Completed(0)

    monkeypatch.setattr(kc.subprocess, "run", fake_run)
    return {"calls": calls, "responses": responses}


def _module(**overrides) -> KubectlChaosModule:
    config = {
        "client": "kubectl",
        "default_namespace": "lab",
        "kubectl_timeout_s": 15,
    }
    config.update(overrides)
    return KubectlChaosModule(config)


class TestCommandPrefix:
    def test_context_and_kubeconfig_are_passed(self, tmp_path: Path):
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
        module = _module(kubeconfig=str(kubeconfig), context="kind-lab")

        cmd = module._base_cmd()
        assert cmd[0] == "kubectl"
        assert "--kubeconfig" in cmd and "--context" in cmd
        assert cmd[cmd.index("--context") + 1] == "kind-lab"
        assert module.validate_config() is True

    def test_missing_kubeconfig_is_not_configured(self, tmp_path: Path):
        module = _module(kubeconfig=str(tmp_path / "absent"))
        assert module.validate_config() is False
        assert module.get_status()["configured"] is False

    def test_bastion_override_adds_server_flags(self, tmp_path: Path):
        module = _module()
        module._api_server_override = "https://127.0.0.1:16443"
        module.ssh_skip_tls_verify = True
        assert "--insecure-skip-tls-verify" in module._base_cmd()

        module.ssh_skip_tls_verify = False
        module._tls_server_name = "cluster.internal"
        cmd = module._base_cmd()
        assert cmd[cmd.index("--tls-server-name") + 1] == "cluster.internal"

    def test_bastion_is_not_attempted_without_host(self):
        module = _module()
        assert module._should_try_bastion() is False
        assert module._maybe_start_bastion(force=False) == {
            "success": True,
            "skipped": True,
        }
        assert module._maybe_start_bastion(force=True)["success"] is False

    def test_bastion_auto_on_fail_flag(self):
        module = _module(ssh_bastion={"host": "jump.lab", "auto_on_api_fail": True})
        assert module._should_try_bastion() is True

        module = _module(ssh_bastion={"host": "jump.lab", "auto_on_api_fail": False})
        assert module._should_try_bastion() is False

    def test_malformed_ssh_config_is_ignored(self):
        module = _module(ssh_bastion="not-a-dict")
        assert module.ssh_enabled is False

    def test_status_and_actions(self):
        module = _module()
        assert module.get_status()["client"] == "kubectl"
        assert "gc_ephemeral" in module.get_available_actions()


class TestRunGuards:
    def test_dry_run_blocks_mutations_only(self, recorder, tmp_path: Path):
        manifest = tmp_path / "chaos.yaml"
        manifest.write_text("kind: PodChaos\n", encoding="utf-8")
        module = _module(dry_run=True)

        applied = module.execute("apply_manifest", {"manifest_path": str(manifest)})
        assert applied["dry_run"] is True
        assert recorder["calls"] == []

        module.execute("count_pods_for_selector", {"label_selector": {"app": "checkout"}})
        assert recorder["calls"], "read-only calls must still reach kubectl"

    def test_timeout_is_flagged(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=15, output="partial")

        monkeypatch.setattr(kc.subprocess, "run", fake_run)
        result = _module().execute("test_connection", {})

        assert result["success"] is False
        assert result["timeout"] is True
        assert result["stdout"] == "partial"

    def test_missing_kubectl_binary(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError(2, "kubectl")

        monkeypatch.setattr(kc.subprocess, "run", fake_run)
        result = _module().execute("test_connection", {})

        assert result["success"] is False
        assert "kubectl binary not found" in result["error"]

    def test_nonzero_exit_carries_error(self, recorder):
        recorder["responses"].append(_Completed(1, stderr="forbidden"))
        result = _module().execute("test_connection", {})

        assert result["success"] is False
        assert result["error"] == "forbidden"

    def test_unknown_action(self, recorder):
        result = _module().execute("nuke", {})
        assert result["success"] is False
        assert "Unknown action" in result["error"]

    def test_native_mode_reports_unavailable(self, monkeypatch):
        module = _module(client="native")
        monkeypatch.setattr(
            module,
            "_native_backend",
            lambda: (_ for _ in ()).throw(kc.NativeUnavailable("no kubeconfig")),
        )
        result = module.execute("test_connection", {})

        assert result["success"] is False
        assert result["backend"] == "native"


class TestReadActions:
    def test_test_connection_message(self, recorder):
        result = _module().execute("test_connection", {})
        assert result["message"] == "OK — cluster reachable"
        assert recorder["calls"][0] == ["kubectl", "get", "ns", "-o", "name"]

    def test_list_contexts_splits_lines(self, recorder):
        recorder["responses"].append(_Completed(0, stdout="kind-lab\nprod\n\n"))
        result = _module().execute("list_contexts", {})
        assert result["contexts"] == ["kind-lab", "prod"]

    def test_list_workloads_dedupes_and_sorts(self, recorder):
        payload = {
            "items": [
                {"metadata": {"name": "checkout"}},
                {"metadata": {"name": "api"}},
                {"metadata": {"name": "checkout"}},
                {"metadata": {}},
            ]
        }
        recorder["responses"].append(_Completed(0, stdout=json.dumps(payload)))
        result = _module().execute("list_workloads", {})

        assert result["workloads"] == ["api", "checkout"]
        assert result["namespace"] == "lab"

    def test_list_workloads_invalid_json(self, recorder):
        recorder["responses"].append(_Completed(0, stdout="{not json"))
        result = _module().execute("list_workloads", {"namespace": "shop"})

        assert result["success"] is False
        assert "invalid kubectl json" in result["error"]

    def test_list_workloads_propagates_failure(self, recorder):
        recorder["responses"].append(_Completed(1, stderr="no such namespace"))
        result = _module().execute("list_workloads", {})
        assert result["success"] is False

    def test_count_pods_requires_selector(self, recorder):
        result = _module().execute("count_pods_for_selector", {})
        assert result["success"] is False
        assert "label_selector required" in result["error"]

    def test_count_pods_counts_names(self, recorder):
        recorder["responses"].append(
            _Completed(0, stdout="pod/checkout-1\npod/checkout-2\n")
        )
        result = _module().execute(
            "count_pods_for_selector", {"label_selector": {"app": "checkout"}}
        )

        assert result["count"] == 2
        assert "app=checkout" in recorder["calls"][0]

    def test_count_pods_propagates_failure(self, recorder):
        recorder["responses"].append(_Completed(1, stderr="denied"))
        result = _module().execute(
            "count_pods_for_selector", {"label_selector": "app=checkout"}
        )
        assert result["success"] is False


class TestApplyAndDelete:
    def test_apply_requires_existing_manifest(self, recorder, tmp_path: Path):
        module = _module()
        assert "manifest_path required" in module.execute("apply_manifest", {})["error"]
        missing = module.execute("apply_manifest", {"path": str(tmp_path / "nope.yaml")})
        assert "manifest not found" in missing["error"]

    def test_apply_manifest(self, recorder, tmp_path: Path):
        manifest = tmp_path / "chaos.yaml"
        manifest.write_text("kind: PodChaos\n", encoding="utf-8")
        result = _module().execute("apply_manifest", {"manifest_path": str(manifest)})

        assert result["success"] is True
        assert recorder["calls"][0][-3:] == ["apply", "-f", str(manifest)]

    def test_delete_requires_target(self, recorder):
        result = _module().execute("delete_manifest", {})
        assert "manifest_path or kind/name required" in result["error"]

    def test_delete_by_manifest(self, recorder, tmp_path: Path):
        manifest = tmp_path / "chaos.yaml"
        manifest.write_text("kind: PodChaos\n", encoding="utf-8")
        result = _module().execute("delete_manifest", {"manifest_path": str(manifest)})

        assert result["success"] is True
        assert len(recorder["calls"]) == 1

    def test_stuck_delete_falls_back_to_force(self, recorder, tmp_path: Path):
        """Chaos Mesh finalizers can hang delete; force sweep prevents orphans."""
        manifest = tmp_path / "chaos.yaml"
        manifest.write_text("kind: PodChaos\n", encoding="utf-8")
        recorder["responses"].extend([_Completed(1, stderr="timed out"), _Completed(0)])

        result = _module().execute(
            "delete_manifest",
            {
                "manifest_path": str(manifest),
                "kind": "podchaos",
                "name": "chaos-1",
                "namespace": "shop",
            },
        )

        assert result["success"] is True
        assert "--force" in recorder["calls"][1]
        assert "--grace-period=0" in recorder["calls"][1]

    def test_force_fallback_disabled_returns_failure(self, recorder, tmp_path: Path):
        manifest = tmp_path / "chaos.yaml"
        manifest.write_text("kind: PodChaos\n", encoding="utf-8")
        recorder["responses"].append(_Completed(1, stderr="timed out"))

        module = _module(delete_force_on_timeout=False)
        result = module.execute(
            "delete_manifest",
            {"manifest_path": str(manifest), "kind": "podchaos", "name": "c1"},
        )

        assert result["success"] is False
        assert len(recorder["calls"]) == 1

    def test_failed_delete_without_kind_name_is_returned(self, recorder, tmp_path: Path):
        manifest = tmp_path / "chaos.yaml"
        manifest.write_text("kind: PodChaos\n", encoding="utf-8")
        recorder["responses"].append(_Completed(1, stderr="timed out"))

        result = _module().execute("delete_manifest", {"manifest_path": str(manifest)})
        assert result["success"] is False

    def test_delete_by_kind_name(self, recorder):
        result = _module().execute(
            "delete_manifest", {"kind": "networkchaos", "name": "chaos-2"}
        )
        argv = recorder["calls"][0]
        assert "networkchaos/chaos-2" in argv
        assert "--ignore-not-found=true" in argv
        assert result["success"] is True

    def test_delete_pod_by_dict_selector(self, recorder):
        _module().execute(
            "delete_pod", {"label_selector": {"app": "checkout", "tier": "web"}}
        )
        argv = recorder["calls"][0]
        assert "app=checkout,tier=web" in argv
        assert "--wait=false" in argv

    def test_delete_pod_by_name(self, recorder):
        _module().execute("delete_pod", {"pod": "checkout-1"})
        assert "checkout-1" in recorder["calls"][0]

    def test_delete_pod_requires_target(self, recorder):
        result = _module().execute("delete_pod", {})
        assert "pod name or label_selector required" in result["error"]


class TestEphemeralSweep:
    def test_gc_uses_label_selector_across_namespaces(self, recorder):
        result = _module().execute("gc_ephemeral", {})
        argv = recorder["calls"][0]

        assert "-A" in argv
        assert CHAOS_KINDS in argv
        assert result["selector"] == (
            f"{MANAGED_BY_LABEL}=chaosgen,{EPHEMERAL_LABEL}=true"
        )

    def test_gc_scoped_to_namespace(self, recorder):
        _module().execute("gc_ephemeral", {"namespace": "shop"})
        argv = recorder["calls"][0]
        assert argv[argv.index("-n") + 1] == "shop"
        assert "-A" not in argv

    def test_gc_list_only_delegates_to_list(self, recorder):
        recorder["responses"].append(_Completed(0, stdout="{}"))
        result = _module().execute("gc_ephemeral", {"list_only": True})

        assert recorder["calls"][0][1] == "get"
        assert "delete" not in recorder["calls"][0]
        assert result["count"] == 0

    def test_list_ephemeral_parses_items(self, recorder):
        payload = {
            "items": [
                {"kind": "PodChaos", "metadata": {"name": "c1", "namespace": "shop"}},
                {"kind": "NetworkChaos", "metadata": {"name": "c2"}},
            ]
        }
        recorder["responses"].append(_Completed(0, stdout=json.dumps(payload)))
        result = _module().execute("list_ephemeral", {"namespace": "shop"})

        assert result["count"] == 2
        assert result["items"][0] == {
            "kind": "PodChaos",
            "name": "c1",
            "namespace": "shop",
        }
        assert result["items"][1]["namespace"] == "shop"
        assert "2 ephemeral" in result["message"]

    def test_list_ephemeral_tolerates_invalid_json(self, recorder):
        recorder["responses"].append(_Completed(0, stdout="{not json"))
        result = _module().execute("list_ephemeral", {})

        assert result["count"] == 0
        assert result["items"] == []
