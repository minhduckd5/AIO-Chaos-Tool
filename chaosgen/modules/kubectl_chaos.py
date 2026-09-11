"""
Kubeconfig-backed chaos module — real cluster mutations.

Phase C prefers the official ``kubernetes`` Python client. subprocess kubectl
remains the fallback when the client cannot load config. Optional OpenSSH
bastion rewrites the API server to 127.0.0.1 when the control plane is not
reachable from the GUI host.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .base import BaseChaosModule
from .k8s_native import NativeK8sBackend, NativeUnavailable, peek_cluster_server
from .ssh_bastion import SshBastionTunnel

logger = logging.getLogger(__name__)

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
EPHEMERAL_LABEL = "chaosgen.io/ephemeral"
CHAOS_KINDS = "networkchaos,podchaos,stresschaos,iochaos,httpchaos"


class KubectlChaosModule(BaseChaosModule):
    """Execute kubectl apply/delete/get against a remote cluster."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.kubeconfig = self._expand(self.config.get("kubeconfig"))
        self.context = self.config.get("context")
        self.default_namespace = self.config.get("default_namespace", "default")
        self.dry_run = bool(self.config.get("dry_run", False))
        self.timeout_s = int(self.config.get("kubectl_timeout_s", 30))
        self.delete_force_on_timeout = bool(
            self.config.get("delete_force_on_timeout", True)
        )
        self.managed_by = self.config.get("managed_by_label", "chaosgen")
        self.ephemeral = str(self.config.get("ephemeral_label", "true"))
        self.last_rollback_status: Optional[str] = None
        self.client_mode = str(self.config.get("client", "auto") or "auto")
        self.last_backend: Optional[str] = None
        self._native: Optional[NativeK8sBackend] = None
        ssh_cfg = self.config.get("ssh_bastion") or {}
        if not isinstance(ssh_cfg, dict):
            ssh_cfg = {}
        self.ssh_enabled = bool(ssh_cfg.get("enabled", False))
        self.ssh_auto_on_fail = bool(ssh_cfg.get("auto_on_api_fail", True))
        self.ssh_host = ssh_cfg.get("host")
        self.ssh_user = ssh_cfg.get("user")
        self.ssh_port = int(ssh_cfg.get("port") or 22)
        self.ssh_identity_file = ssh_cfg.get("identity_file")
        self.ssh_remote_api_host = ssh_cfg.get("remote_api_host") or "127.0.0.1"
        self.ssh_remote_api_port = int(ssh_cfg.get("remote_api_port") or 6443)
        self.ssh_local_port = int(ssh_cfg.get("local_port") or 0)
        self.ssh_skip_tls_verify = bool(ssh_cfg.get("skip_tls_verify", False))
        self._tunnel: Optional[SshBastionTunnel] = None
        self._api_server_override: Optional[str] = None
        self._tls_server_name: Optional[str] = None

    def reload_from_config(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Re-bind instance fields after Settings Save (mod.config alone is not enough)."""
        # --- START MODIFICATION ---
        if config is not None:
            self.config = {**getattr(self, "config", {}), **config}
        self.kubeconfig = self._expand(self.config.get("kubeconfig"))
        self.context = self.config.get("context")
        self.default_namespace = self.config.get("default_namespace", "default")
        self.dry_run = bool(self.config.get("dry_run", False))
        self.timeout_s = int(self.config.get("kubectl_timeout_s", 30))
        self.delete_force_on_timeout = bool(
            self.config.get("delete_force_on_timeout", True)
        )
        self.managed_by = self.config.get("managed_by_label", "chaosgen")
        self.ephemeral = str(self.config.get("ephemeral_label", "true"))
        self.client_mode = str(self.config.get("client", "auto") or "auto")
        self._native = None
        self.last_backend = None
        ssh_cfg = self.config.get("ssh_bastion") or {}
        if not isinstance(ssh_cfg, dict):
            ssh_cfg = {}
        self.ssh_enabled = bool(ssh_cfg.get("enabled", False))
        self.ssh_auto_on_fail = bool(ssh_cfg.get("auto_on_api_fail", True))
        self.ssh_host = ssh_cfg.get("host")
        self.ssh_user = ssh_cfg.get("user")
        self.ssh_port = int(ssh_cfg.get("port") or 22)
        self.ssh_identity_file = ssh_cfg.get("identity_file")
        self.ssh_remote_api_host = ssh_cfg.get("remote_api_host") or "127.0.0.1"
        self.ssh_remote_api_port = int(ssh_cfg.get("remote_api_port") or 6443)
        self.ssh_local_port = int(ssh_cfg.get("local_port") or 0)
        self.ssh_skip_tls_verify = bool(ssh_cfg.get("skip_tls_verify", False))
        if self._tunnel is not None:
            try:
                self._tunnel.close()
            except Exception:
                pass
            self._tunnel = None
        self._api_server_override = None
        self._tls_server_name = None
        # --- END MODIFICATION ---

    @staticmethod
    def _expand(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        return str(Path(os.path.expanduser(path)).resolve())

    def validate_config(self) -> bool:
        if self.kubeconfig and not Path(self.kubeconfig).is_file():
            logger.warning("inject.kubeconfig not found: %s", self.kubeconfig)
            return False
        return True

    def _base_cmd(self) -> List[str]:
        cmd = ["kubectl"]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])
        if self.context:
            cmd.extend(["--context", self.context])
        if self._api_server_override:
            cmd.extend(["--server", self._api_server_override])
            if self.ssh_skip_tls_verify:
                cmd.append("--insecure-skip-tls-verify")
            elif self._tls_server_name:
                cmd.extend(["--tls-server-name", self._tls_server_name])
        return cmd

    def _run(
        self,
        args: List[str],
        *,
        timeout: Optional[int] = None,
        check_dry: bool = True,
    ) -> Dict[str, Any]:
        cmd = self._base_cmd() + args
        t = timeout if timeout is not None else self.timeout_s
        if check_dry and self.dry_run and args and args[0] in ("apply", "delete", "create"):
            logger.info("DRY-RUN kubectl: %s", " ".join(cmd))
            return {
                "success": True,
                "dry_run": True,
                "module": "kubectl-chaos",
                "cmd": cmd,
                "stdout": "",
                "stderr": "",
                "message": f"dry-run: {' '.join(cmd)}",
            }
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=t,
                check=False,
            )
            ok = completed.returncode == 0
            result = {
                "success": ok,
                "dry_run": False,
                "module": "kubectl-chaos",
                "cmd": cmd,
                "returncode": completed.returncode,
                "stdout": completed.stdout or "",
                "stderr": completed.stderr or "",
                "message": (completed.stdout or completed.stderr or "").strip(),
            }
            if not ok:
                result["error"] = result["message"] or f"kubectl exit {completed.returncode}"
            return result
        except subprocess.TimeoutExpired as exc:
            logger.error("kubectl timed out after %ss: %s", t, " ".join(cmd))
            return {
                "success": False,
                "dry_run": False,
                "module": "kubectl-chaos",
                "cmd": cmd,
                "error": f"timeout after {t}s",
                "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
                "timeout": True,
            }
        except FileNotFoundError:
            return {
                "success": False,
                "module": "kubectl-chaos",
                "error": "kubectl binary not found on PATH",
                "cmd": cmd,
            }

    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params or {})
        if action == "test_connection":
            return self._test_connection_with_bastion(params)

        self._maybe_start_bastion(force=False)
        result = self._dispatch(action, params)
        if (
            not result.get("success")
            and self._should_try_bastion()
            and not self._tunnel
        ):
            started = self._start_bastion()
            if started.get("success"):
                result = self._dispatch(action, params)
        return result

    def _dispatch(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.client_mode in ("auto", "native"):
            try:
                result = self._native_backend().execute(action, params)
                self.last_backend = "native"
                return result
            except NativeUnavailable as exc:
                if self.client_mode == "native":
                    return {
                        "success": False,
                        "backend": "native",
                        "error": str(exc),
                    }
                logger.info("native client unavailable (%s); using kubectl", exc)

        actions = {
            "test_connection": self._test_connection,
            "list_workloads": self._list_workloads,
            "list_contexts": self._list_contexts,
            "apply_manifest": self._apply_manifest,
            "delete_manifest": self._delete_manifest,
            "delete_pod": self._delete_pod,
            "count_pods_for_selector": self._count_pods,
            "gc_ephemeral": self._gc_ephemeral,
            "list_ephemeral": self._list_ephemeral,
        }
        handler = actions.get(action)
        if not handler:
            return {"success": False, "error": f"Unknown action: {action}"}
        result = handler(params)
        result.setdefault("backend", "kubectl")
        self.last_backend = "kubectl"
        return result

    def _native_backend(self) -> NativeK8sBackend:
        if self._native is None:
            self._native = NativeK8sBackend(
                kubeconfig=self.kubeconfig,
                context=self.context,
                timeout_s=self.timeout_s,
                dry_run=self.dry_run,
                server_override=self._api_server_override,
                tls_server_name=self._tls_server_name,
                skip_tls_verify=self.ssh_skip_tls_verify,
                managed_by=self.managed_by,
                ephemeral=self.ephemeral,
                default_namespace=self.default_namespace,
                delete_force_on_timeout=self.delete_force_on_timeout,
            )
        else:
            self._native.kubeconfig = self.kubeconfig
            self._native.context = self.context
            self._native.dry_run = self.dry_run
            self._native.timeout_s = self.timeout_s
            self._native.server_override = self._api_server_override
            self._native.tls_server_name = self._tls_server_name
            self._native.skip_tls_verify = self.ssh_skip_tls_verify
            self._native.default_namespace = self.default_namespace
        return self._native

    def invalidate_client(self) -> None:
        if self._native:
            self._native.invalidate()
        self._native = None

    def _should_try_bastion(self) -> bool:
        if not self.ssh_host:
            return False
        if self.ssh_enabled:
            return True
        return self.ssh_auto_on_fail

    def _maybe_start_bastion(self, *, force: bool) -> Dict[str, Any]:
        if not force and not self.ssh_enabled:
            return {"success": True, "skipped": True}
        if not self.ssh_host:
            return {"success": False, "error": "ssh bastion host not set"}
        if self._tunnel and self._tunnel.is_alive():
            return {"success": True, "local_port": self._tunnel.local_port}
        return self._start_bastion()

    def _start_bastion(self) -> Dict[str, Any]:
        original = peek_cluster_server(self.kubeconfig, self.context)
        if original:
            parsed = urlparse(original)
            self._tls_server_name = parsed.hostname
        tunnel = SshBastionTunnel(
            host=self.ssh_host,
            user=self.ssh_user,
            port=self.ssh_port,
            identity_file=self.ssh_identity_file,
            remote_api_host=self.ssh_remote_api_host,
            remote_api_port=self.ssh_remote_api_port,
            local_port=self.ssh_local_port,
        )
        result = tunnel.start(wait_s=min(20, max(5, self.timeout_s)))
        if not result.get("success"):
            return result
        self._tunnel = tunnel
        self._api_server_override = f"https://127.0.0.1:{tunnel.local_port}"
        self.invalidate_client()
        result["server"] = self._api_server_override
        return result

    def _test_connection_with_bastion(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.ssh_enabled and self.ssh_host:
            started = self._start_bastion()
            if not started.get("success"):
                return started
        result = self._dispatch("test_connection", params)
        if result.get("success"):
            return result
        if self._should_try_bastion() and not (self._tunnel and self._tunnel.is_alive()):
            started = self._start_bastion()
            if started.get("success"):
                retry = self._dispatch("test_connection", params)
                if retry.get("success"):
                    retry["message"] = (
                        (retry.get("message") or "OK") + " via SSH bastion"
                    )
                    retry["bastion"] = started
                else:
                    retry["bastion"] = started
                return retry
            result["bastion"] = started
        return result

    def _test_connection(self, params: Dict[str, Any]) -> Dict[str, Any]:
        result = self._run(["get", "ns", "-o", "name"], check_dry=False)
        if result.get("success"):
            result["message"] = "OK — cluster reachable"
        return result

    def _list_contexts(self, params: Dict[str, Any]) -> Dict[str, Any]:
        result = self._run(
            ["config", "get-contexts", "-o", "name"],
            check_dry=False,
        )
        if result.get("success"):
            names = [
                line.strip()
                for line in (result.get("stdout") or "").splitlines()
                if line.strip()
            ]
            result["contexts"] = names
        return result

    def _list_workloads(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ns = params.get("namespace") or self.default_namespace
        result = self._run(
            ["get", "deploy,sts", "-n", ns, "-o", "json"],
            check_dry=False,
        )
        if not result.get("success"):
            return result
        names: List[str] = []
        try:
            payload = json.loads(result.get("stdout") or "{}")
            for item in payload.get("items") or []:
                meta = item.get("metadata") or {}
                name = meta.get("name")
                if name:
                    names.append(name)
        except json.JSONDecodeError as exc:
            return {"success": False, "error": f"invalid kubectl json: {exc}"}
        result["workloads"] = sorted(set(names))
        result["namespace"] = ns
        return result

    def _apply_manifest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params.get("manifest_path") or params.get("path")
        if not path:
            return {"success": False, "error": "manifest_path required"}
        if not Path(path).is_file():
            return {"success": False, "error": f"manifest not found: {path}"}
        return self._run(["apply", "-f", str(path)])

    def _delete_manifest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params.get("manifest_path") or params.get("path")
        kind = params.get("kind")
        name = params.get("name")
        ns = params.get("namespace") or self.default_namespace
        timeout = params.get("timeout", self.timeout_s)

        if path and Path(path).is_file():
            result = self._run(
                ["delete", "-f", str(path), f"--timeout={timeout}s", "--wait=true"],
                timeout=int(timeout) + 5,
            )
            if result.get("success") or not self.delete_force_on_timeout:
                return result
            if kind and name:
                return self._run(
                    [
                        "delete",
                        f"{kind}/{name}",
                        "-n",
                        ns,
                        "--force",
                        "--grace-period=0",
                        "--ignore-not-found=true",
                    ],
                    timeout=int(timeout),
                )
            return result

        if kind and name:
            return self._run(
                [
                    "delete",
                    f"{kind}/{name}",
                    "-n",
                    ns,
                    f"--timeout={timeout}s",
                    "--ignore-not-found=true",
                ],
                timeout=int(timeout) + 5,
            )
        return {"success": False, "error": "manifest_path or kind/name required"}

    def _delete_pod(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ns = params.get("namespace") or self.default_namespace
        label_selector = params.get("label_selector")
        pod = params.get("pod") or params.get("name")
        # --- START MODIFICATION ---
        # P0: refuse silent no-op when selector/name matches zero pods.
        if label_selector:
            if isinstance(label_selector, dict):
                label_selector = ",".join(f"{k}={v}" for k, v in label_selector.items())
            counted = self._count_pods(
                {"namespace": ns, "label_selector": str(label_selector)}
            )
            if not counted.get("success"):
                return counted
            if int(counted.get("count") or 0) == 0:
                return {
                    "success": False,
                    "error": "no pods matched selector, nothing injected",
                    "no_target": True,
                    "matched_count": 0,
                    "label_selector": str(label_selector),
                    "namespace": ns,
                    "module": "kubectl-chaos",
                    "backend": "kubectl",
                }
            result = self._run(
                [
                    "delete",
                    "pod",
                    "-n",
                    ns,
                    "-l",
                    str(label_selector),
                    "--wait=false",
                ]
            )
            result["matched_count"] = int(counted.get("count") or 0)
            result["no_target"] = False
            return result
        if pod:
            probe = self._run(
                ["get", "pod", str(pod), "-n", ns, "-o", "name"],
                check_dry=False,
            )
            if not probe.get("success") or not (probe.get("stdout") or "").strip():
                return {
                    "success": False,
                    "error": "no pods matched selector, nothing injected",
                    "no_target": True,
                    "matched_count": 0,
                    "pod": str(pod),
                    "namespace": ns,
                    "module": "kubectl-chaos",
                    "backend": "kubectl",
                }
            result = self._run(
                ["delete", "pod", str(pod), "-n", ns, "--wait=false"]
            )
            result["matched_count"] = 1
            result["no_target"] = False
            return result
        # --- END MODIFICATION ---
        return {"success": False, "error": "pod name or label_selector required"}

    def _count_pods(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ns = params.get("namespace") or self.default_namespace
        label_selector = params.get("label_selector")
        if isinstance(label_selector, dict):
            label_selector = ",".join(f"{k}={v}" for k, v in label_selector.items())
        if not label_selector:
            return {"success": False, "error": "label_selector required"}
        result = self._run(
            ["get", "pods", "-n", ns, "-l", str(label_selector), "-o", "name"],
            check_dry=False,
        )
        if not result.get("success"):
            return result
        lines = [
            line for line in (result.get("stdout") or "").splitlines() if line.strip()
        ]
        result["count"] = len(lines)
        return result

    def _gc_ephemeral(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Delete Chaos CRs owned by ChaosGen (label sweep — no local state needed)."""
        managed = params.get("managed_by") or self.managed_by
        ephemeral = params.get("ephemeral") or self.ephemeral
        selector = f"{MANAGED_BY_LABEL}={managed},{EPHEMERAL_LABEL}={ephemeral}"
        kinds = params.get("kinds") or CHAOS_KINDS
        ns = params.get("namespace")
        if params.get("list_only"):
            return self._list_ephemeral(params)
        args = ["delete", kinds, "-l", selector, "--ignore-not-found=true"]
        if ns:
            args.extend(["-n", ns])
        else:
            args.append("-A")
        result = self._run(args)
        result["selector"] = selector
        return result

    def _list_ephemeral(self, params: Dict[str, Any]) -> Dict[str, Any]:
        managed = params.get("managed_by") or self.managed_by
        ephemeral = params.get("ephemeral") or self.ephemeral
        selector = f"{MANAGED_BY_LABEL}={managed},{EPHEMERAL_LABEL}={ephemeral}"
        kinds = params.get("kinds") or CHAOS_KINDS
        ns = params.get("namespace")
        args = ["get", kinds, "-l", selector, "-o", "json"]
        if ns:
            args.extend(["-n", ns])
        else:
            args.append("-A")
        result = self._run(args, check_dry=False)
        result["selector"] = selector
        items: List[Dict[str, str]] = []
        if result.get("success"):
            try:
                payload = json.loads(result.get("stdout") or "{}")
                for raw in payload.get("items") or []:
                    meta = raw.get("metadata") or {}
                    items.append(
                        {
                            "kind": raw.get("kind") or "",
                            "name": meta.get("name") or "",
                            "namespace": meta.get("namespace") or ns or "",
                        }
                    )
            except json.JSONDecodeError:
                pass
        result["items"] = items
        result["count"] = len(items)
        result["message"] = f"{len(items)} ephemeral Chaos Mesh CR(s)"
        return result

    def get_available_actions(self) -> List[str]:
        return [
            "test_connection",
            "list_workloads",
            "list_contexts",
            "apply_manifest",
            "delete_manifest",
            "delete_pod",
            "count_pods_for_selector",
            "gc_ephemeral",
            "list_ephemeral",
        ]

    def get_status(self) -> Dict[str, Any]:
        return {
            "module": "kubectl-chaos",
            "configured": self.validate_config(),
            "kubeconfig": self.kubeconfig,
            "context": self.context,
            "dry_run": self.dry_run,
            "timeout_s": self.timeout_s,
            "default_namespace": self.default_namespace,
            "client": self.client_mode,
            "backend": self.last_backend,
            "bastion": bool(self._tunnel and self._tunnel.is_alive()),
            "server_override": self._api_server_override,
        }
