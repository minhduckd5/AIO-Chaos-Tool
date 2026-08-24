"""
Kubectl-backed chaos module — real cluster mutations via kubeconfig.

MVP uses subprocess kubectl (intentional duct-tape). Fail-closed: hard timeouts,
mandatory duration on Chaos Mesh CRs (enforced upstream), and label taxonomy so
``chaosgen inject-gc`` can sweep orphans without a local state file.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseChaosModule

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
        actions = {
            "test_connection": self._test_connection,
            "list_workloads": self._list_workloads,
            "list_contexts": self._list_contexts,
            "apply_manifest": self._apply_manifest,
            "delete_manifest": self._delete_manifest,
            "delete_pod": self._delete_pod,
            "count_pods_for_selector": self._count_pods,
            "gc_ephemeral": self._gc_ephemeral,
        }
        handler = actions.get(action)
        if not handler:
            return {"success": False, "error": f"Unknown action: {action}"}
        return handler(params)

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
        if label_selector:
            if isinstance(label_selector, dict):
                label_selector = ",".join(f"{k}={v}" for k, v in label_selector.items())
            return self._run(
                [
                    "delete",
                    "pod",
                    "-n",
                    ns,
                    "-l",
                    str(label_selector),
                    "--wait=false",
                    "--ignore-not-found=true",
                ]
            )
        if pod:
            return self._run(
                ["delete", "pod", pod, "-n", ns, "--wait=false", "--ignore-not-found=true"]
            )
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
        args = ["delete", kinds, "-l", selector, "--ignore-not-found=true"]
        if ns:
            args.extend(["-n", ns])
        else:
            args.append("-A")
        return self._run(args)

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
        }
