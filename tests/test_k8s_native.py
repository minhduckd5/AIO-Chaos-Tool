"""Phase C — native kubernetes client, SSH bastion, inject-gc listing."""

from __future__ import annotations

from unittest.mock import MagicMock

import yaml

from chaosgen.config.settings import InjectSettings, SshBastionSettings, load_settings
from chaosgen.modules.k8s_native import NativeK8sBackend, NativeUnavailable, peek_cluster_server
from chaosgen.modules.kubectl_chaos import KubectlChaosModule
from chaosgen.modules.ssh_bastion import SshBastionTunnel


def test_peek_cluster_server(tmp_path):
    kube = tmp_path / "config"
    kube.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "lab",
                "contexts": [{"name": "lab", "context": {"cluster": "k3s", "user": "u"}}],
                "clusters": [
                    {
                        "name": "k3s",
                        "cluster": {"server": "https://192.168.31.220:6443"},
                    }
                ],
                "users": [{"name": "u", "user": {"token": "x"}}],
            }
        ),
        encoding="utf-8",
    )
    assert peek_cluster_server(str(kube), None) == "https://192.168.31.220:6443"
    assert peek_cluster_server(str(kube), "lab") == "https://192.168.31.220:6443"


def test_bastion_build_cmd():
    tunnel = SshBastionTunnel(
        host="192.168.31.220",
        user="root",
        identity_file=None,
        remote_api_host="127.0.0.1",
        remote_api_port=6443,
        local_port=16443,
    )
    cmd = tunnel.build_cmd()
    assert cmd[0] == "ssh"
    assert "-L" in cmd
    assert "16443:127.0.0.1:6443" in cmd
    assert "root@192.168.31.220" in cmd
    assert "BatchMode=yes" in cmd


def test_native_apply_dry_run(tmp_path):
    manifest = tmp_path / "net.yaml"
    manifest.write_text("kind: NetworkChaos\nmetadata:\n  name: x\n", encoding="utf-8")
    backend = NativeK8sBackend(dry_run=True)
    result = backend.apply_manifest({"manifest_path": str(manifest)})
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["backend"] == "native"


def test_native_gc_dry_run_lists_without_delete():
    backend = NativeK8sBackend(dry_run=True, managed_by="chaosgen")
    listed = (
        [{"kind": "NetworkChaos", "name": "n1", "namespace": "default"}],
        [],
    )
    backend._list_chaos = MagicMock(return_value=listed)  # type: ignore[method-assign]
    backend._delete_object = MagicMock()  # type: ignore[method-assign]
    result = backend.gc_ephemeral({})
    assert result["success"] is True
    assert result["count"] == 1
    assert "app.kubernetes.io/managed-by=chaosgen" in result["selector"]
    backend._delete_object.assert_not_called()


def test_native_gc_deletes_listed():
    backend = NativeK8sBackend(dry_run=False)
    backend._list_chaos = MagicMock(  # type: ignore[method-assign]
        return_value=(
            [{"kind": "NetworkChaos", "name": "n1", "namespace": "default"}],
            [],
        )
    )
    backend._delete_object = MagicMock()  # type: ignore[method-assign]
    result = backend.gc_ephemeral({})
    assert result["success"] is True
    assert result["deleted"] == ["default/NetworkChaos/n1"]
    backend._delete_object.assert_called_once()


def test_native_unavailable_falls_back_to_kubectl():
    mod = KubectlChaosModule({"client": "auto", "dry_run": True})
    fake = MagicMock()
    fake.execute.side_effect = NativeUnavailable("no kubeconfig")
    mod._native_backend = MagicMock(return_value=fake)  # type: ignore[method-assign]
    result = mod.execute("apply_manifest", {"manifest_path": __file__})
    assert result["success"] is True
    assert result.get("backend") == "kubectl"
    assert result.get("dry_run") is True


def test_native_mode_does_not_fallback():
    mod = KubectlChaosModule({"client": "native"})
    fake = MagicMock()
    fake.execute.side_effect = NativeUnavailable("missing package")
    mod._native_backend = MagicMock(return_value=fake)  # type: ignore[method-assign]
    result = mod.execute("test_connection", {})
    assert result["success"] is False
    assert "missing package" in result["error"]


def test_inject_settings_ssh_bastion(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        """
inject:
  enabled: true
  client: native
  ssh_bastion:
    enabled: true
    host: 192.168.31.220
    user: root
    remote_api_port: 6443
""",
        encoding="utf-8",
    )
    settings = load_settings(path=str(path))
    assert settings.inject.client == "native"
    assert settings.inject.ssh_bastion.host == "192.168.31.220"
    assert settings.inject.ssh_bastion.user == "root"


def test_inject_settings_defaults():
    inj = InjectSettings()
    assert inj.client == "auto"
    assert isinstance(inj.ssh_bastion, SshBastionSettings)
    assert inj.ssh_bastion.enabled is False


def test_inject_gc_help_lists_kubeconfig():
    from click.testing import CliRunner

    from chaosgen.cli import main

    result = CliRunner().invoke(main, ["inject-gc", "--help"])
    assert result.exit_code == 0
    assert "--kubeconfig" in result.output
    assert "--list-only" in result.output
