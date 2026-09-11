"""
Official ``kubernetes`` Python client backend for ChaosGen inject.

Same actions as KubectlChaosModule (apply/delete/gc/list). kubectl subprocess
remains the fallback when this backend cannot load config.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
EPHEMERAL_LABEL = "chaosgen.io/ephemeral"
CHAOS_GROUP = "chaos-mesh.org"
CHAOS_VERSION = "v1alpha1"
CHAOS_PLURALS = (
    "networkchaos",
    "podchaos",
    "stresschaos",
    "iochaos",
    "httpchaos",
)
KIND_TO_PLURAL = {
    "NetworkChaos": "networkchaos",
    "PodChaos": "podchaos",
    "StressChaos": "stresschaos",
    "IOChaos": "iochaos",
    "HTTPChaos": "httpchaos",
}


def peek_cluster_server(kubeconfig: Optional[str], context: Optional[str]) -> Optional[str]:
    """Read cluster.server from a kubeconfig without contacting the API."""
    path = Path(kubeconfig) if kubeconfig else Path.home() / ".kube" / "config"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    ctx_name = context or data.get("current-context")
    cluster_name = None
    for ctx in data.get("contexts") or []:
        if ctx.get("name") == ctx_name:
            cluster_name = (ctx.get("context") or {}).get("cluster")
            break
    if not cluster_name:
        return None
    for cluster in data.get("clusters") or []:
        if cluster.get("name") == cluster_name:
            return (cluster.get("cluster") or {}).get("server")
    return None


def _label_selector(managed_by: str, ephemeral: str) -> str:
    return f"{MANAGED_BY_LABEL}={managed_by},{EPHEMERAL_LABEL}={ephemeral}"


def _as_selector(label_selector: Any) -> str:
    if isinstance(label_selector, dict):
        return ",".join(f"{k}={v}" for k, v in label_selector.items())
    return str(label_selector)


class NativeUnavailable(RuntimeError):
    """Raised when the official client cannot be used; caller may fall back."""


class NativeK8sBackend:
    """Apply/delete Chaos Mesh CRs and pods via kubernetes.client."""

    def __init__(
        self,
        *,
        kubeconfig: Optional[str] = None,
        context: Optional[str] = None,
        timeout_s: int = 30,
        dry_run: bool = False,
        server_override: Optional[str] = None,
        tls_server_name: Optional[str] = None,
        skip_tls_verify: bool = False,
        managed_by: str = "chaosgen",
        ephemeral: str = "true",
        default_namespace: str = "default",
        delete_force_on_timeout: bool = True,
        api_client: Any = None,
    ):
        self.kubeconfig = kubeconfig
        self.context = context
        self.timeout_s = int(timeout_s)
        self.dry_run = bool(dry_run)
        self.server_override = server_override
        self.tls_server_name = tls_server_name
        self.skip_tls_verify = bool(skip_tls_verify)
        self.managed_by = managed_by
        self.ephemeral = str(ephemeral)
        self.default_namespace = default_namespace
        self.delete_force_on_timeout = bool(delete_force_on_timeout)
        self._api_client = api_client

    def _client(self) -> Any:
        if self._api_client is not None:
            return self._api_client
        try:
            from kubernetes import client, config as k8s_config
        except ImportError as exc:
            raise NativeUnavailable("kubernetes Python package is not installed") from exc

        try:
            if self.kubeconfig:
                k8s_config.load_kube_config(
                    config_file=self.kubeconfig,
                    context=self.context or None,
                )
            else:
                k8s_config.load_kube_config(context=self.context or None)
        except Exception as exc:
            raise NativeUnavailable(f"failed to load kubeconfig: {exc}") from exc

        configuration = client.Configuration.get_default_copy()
        configuration.retries = 0
        if self.server_override:
            configuration.host = self.server_override.rstrip("/")
        if self.tls_server_name:
            configuration.tls_server_name = self.tls_server_name
        if self.skip_tls_verify:
            configuration.verify_ssl = False
        self._api_client = client.ApiClient(configuration)
        return self._api_client

    def invalidate(self) -> None:
        self._api_client = None

    def _timeout(self) -> int:
        return self.timeout_s

    def _ok(self, **extra: Any) -> Dict[str, Any]:
        payload = {
            "success": True,
            "dry_run": self.dry_run,
            "module": "kubectl-chaos",
            "backend": "native",
        }
        payload.update(extra)
        return payload

    def _fail(self, error: str, **extra: Any) -> Dict[str, Any]:
        payload = {
            "success": False,
            "dry_run": self.dry_run,
            "module": "kubectl-chaos",
            "backend": "native",
            "error": error,
        }
        payload.update(extra)
        return payload

    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "test_connection": self.test_connection,
            "list_workloads": self.list_workloads,
            "list_contexts": self.list_contexts,
            "apply_manifest": self.apply_manifest,
            "delete_manifest": self.delete_manifest,
            "delete_pod": self.delete_pod,
            "count_pods_for_selector": self.count_pods,
            "gc_ephemeral": self.gc_ephemeral,
            "list_ephemeral": self.list_ephemeral,
        }
        handler = handlers.get(action)
        if not handler:
            return self._fail(f"Unknown action: {action}")
        try:
            return handler(params)
        except NativeUnavailable:
            raise
        except Exception as exc:
            logger.exception("native kubernetes action %s failed", action)
            timeout = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
            return self._fail(str(exc), timeout=timeout)

    def test_connection(self, params: Dict[str, Any]) -> Dict[str, Any]:
        from kubernetes import client

        v1 = client.CoreV1Api(self._client())
        v1.list_namespace(limit=1, _request_timeout=self._timeout())
        return self._ok(message="OK — cluster reachable (native client)")

    def list_contexts(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from kubernetes import config as k8s_config
            from kubernetes.config.config_exception import ConfigException
        except ImportError as exc:
            raise NativeUnavailable("kubernetes Python package is not installed") from exc
        try:
            contexts, active = k8s_config.list_kube_config_contexts(
                config_file=self.kubeconfig
            )
        except ConfigException as exc:
            raise NativeUnavailable(str(exc)) from exc
        names = [c["name"] for c in (contexts or []) if c.get("name")]
        return self._ok(contexts=names, active=(active or {}).get("name"))

    def list_workloads(self, params: Dict[str, Any]) -> Dict[str, Any]:
        from kubernetes import client

        ns = params.get("namespace") or self.default_namespace
        apps = client.AppsV1Api(self._client())
        names: List[str] = []
        for item in apps.list_namespaced_deployment(
            ns, _request_timeout=self._timeout()
        ).items:
            if item.metadata and item.metadata.name:
                names.append(item.metadata.name)
        for item in apps.list_namespaced_stateful_set(
            ns, _request_timeout=self._timeout()
        ).items:
            if item.metadata and item.metadata.name:
                names.append(item.metadata.name)
        return self._ok(workloads=sorted(set(names)), namespace=ns)

    def apply_manifest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params.get("manifest_path") or params.get("path")
        if not path:
            return self._fail("manifest_path required")
        if not Path(path).is_file():
            return self._fail(f"manifest not found: {path}")
        if self.dry_run:
            return self._ok(message=f"dry-run: apply {path}", path=str(path))
        docs = [
            doc
            for doc in yaml.safe_load_all(Path(path).read_text(encoding="utf-8"))
            if doc
        ]
        applied: List[str] = []
        for doc in docs:
            self._create_or_replace(doc)
            meta = doc.get("metadata") or {}
            applied.append(f"{doc.get('kind')}/{meta.get('name')}")
        return self._ok(message="applied " + ", ".join(applied), applied=applied)

    def _create_or_replace(self, doc: Dict[str, Any]) -> None:
        from kubernetes.client.rest import ApiException

        kind = doc.get("kind") or ""
        api_version = doc.get("apiVersion") or ""
        meta = doc.get("metadata") or {}
        name = meta.get("name")
        ns = meta.get("namespace") or self.default_namespace
        if not name:
            raise ValueError("manifest metadata.name is required")

        if kind in KIND_TO_PLURAL and api_version.startswith(CHAOS_GROUP):
            custom = self._custom()
            plural = KIND_TO_PLURAL[kind]
            try:
                custom.create_namespaced_custom_object(
                    CHAOS_GROUP,
                    CHAOS_VERSION,
                    ns,
                    plural,
                    doc,
                    _request_timeout=self._timeout(),
                )
                return
            except ApiException as exc:
                if exc.status != 409:
                    raise
                custom.patch_namespaced_custom_object(
                    CHAOS_GROUP,
                    CHAOS_VERSION,
                    ns,
                    plural,
                    name,
                    doc,
                    _request_timeout=self._timeout(),
                )
                return

        from kubernetes.dynamic import DynamicClient

        dyn = DynamicClient(self._client())
        resource = dyn.resources.get(api_version=api_version, kind=kind)
        try:
            if getattr(resource, "namespaced", True):
                resource.create(body=doc, namespace=ns)
            else:
                resource.create(body=doc)
        except ApiException as exc:
            if exc.status != 409:
                raise
            resource.patch(
                body=doc,
                name=name,
                namespace=ns if getattr(resource, "namespaced", True) else None,
                content_type="application/merge-patch+json",
            )

    def _custom(self) -> Any:
        from kubernetes import client

        return client.CustomObjectsApi(self._client())

    def delete_manifest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        path = params.get("manifest_path") or params.get("path")
        kind = params.get("kind")
        name = params.get("name")
        ns = params.get("namespace") or self.default_namespace
        timeout = int(params.get("timeout", self.timeout_s))

        if path and Path(path).is_file() and not (kind and name):
            doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            kind = doc.get("kind")
            name = (doc.get("metadata") or {}).get("name")
            ns = (doc.get("metadata") or {}).get("namespace") or ns

        if not kind or not name:
            return self._fail("manifest_path or kind/name required")
        if self.dry_run:
            return self._ok(message=f"dry-run: delete {kind}/{name}", kind=kind, name=name)

        try:
            self._delete_object(kind, name, ns, force=False, timeout=timeout)
            return self._ok(message=f"deleted {kind}/{name}", kind=kind, name=name)
        except Exception as exc:
            if not self.delete_force_on_timeout:
                return self._fail(str(exc), timeout=True)
            logger.warning("delete timed out/failed; forcing %s/%s: %s", kind, name, exc)
            try:
                self._delete_object(kind, name, ns, force=True, timeout=timeout)
                return self._ok(
                    message=f"force-deleted {kind}/{name}",
                    kind=kind,
                    name=name,
                    forced=True,
                )
            except Exception as force_exc:
                return self._fail(str(force_exc), timeout=True, kind=kind, name=name)

    def _delete_object(
        self,
        kind: str,
        name: str,
        ns: str,
        *,
        force: bool,
        timeout: int,
    ) -> None:
        from kubernetes.client.rest import ApiException

        grace = 0 if force else None
        plural = KIND_TO_PLURAL.get(kind)
        if plural:
            try:
                self._custom().delete_namespaced_custom_object(
                    CHAOS_GROUP,
                    CHAOS_VERSION,
                    ns,
                    plural,
                    name,
                    grace_period_seconds=grace,
                    _request_timeout=timeout,
                )
            except ApiException as exc:
                if exc.status == 404:
                    return
                raise
            self._wait_gone(kind, name, ns, timeout=timeout)
            return

        from kubernetes.dynamic import DynamicClient

        dyn = DynamicClient(self._client())
        # api_version unknown for generic kinds — try common core
        api_version = "v1" if kind in ("Pod", "Service", "Namespace") else "apps/v1"
        resource = dyn.resources.get(api_version=api_version, kind=kind)
        try:
            resource.delete(name=name, namespace=ns, grace_period_seconds=grace or 0)
        except ApiException as exc:
            if getattr(exc, "status", None) == 404:
                return
            raise

    def _wait_gone(self, kind: str, name: str, ns: str, timeout: int) -> None:
        from kubernetes.client.rest import ApiException

        plural = KIND_TO_PLURAL.get(kind)
        if not plural:
            return
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            try:
                self._custom().get_namespaced_custom_object(
                    CHAOS_GROUP,
                    CHAOS_VERSION,
                    ns,
                    plural,
                    name,
                    _request_timeout=5,
                )
                time.sleep(0.4)
            except ApiException as exc:
                if exc.status == 404:
                    return
                raise
        raise TimeoutError(f"timed out waiting for {kind}/{name} deletion")

    def delete_pod(self, params: Dict[str, Any]) -> Dict[str, Any]:
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        ns = params.get("namespace") or self.default_namespace
        label_selector = params.get("label_selector")
        pod = params.get("pod") or params.get("name")
        if self.dry_run:
            return self._ok(message="dry-run: delete pod")
        v1 = client.CoreV1Api(self._client())
        try:
            # --- START MODIFICATION ---
            # P0 invalid_target_false_pass: 0 matches must not report success.
            if label_selector:
                sel = _as_selector(label_selector)
                items = (
                    v1.list_namespaced_pod(
                        ns,
                        label_selector=sel,
                        _request_timeout=self._timeout(),
                    ).items
                    or []
                )
                if not items:
                    return self._fail(
                        "no pods matched selector, nothing injected",
                        no_target=True,
                        matched_count=0,
                        label_selector=sel,
                        namespace=ns,
                    )
                v1.delete_collection_namespaced_pod(
                    ns,
                    label_selector=sel,
                    grace_period_seconds=0,
                    _request_timeout=self._timeout(),
                )
                return self._ok(
                    message=f"deleted {len(items)} pod(s) -l {sel}",
                    matched_count=len(items),
                    label_selector=sel,
                    namespace=ns,
                )
            if pod:
                try:
                    v1.delete_namespaced_pod(
                        pod,
                        ns,
                        grace_period_seconds=0,
                        _request_timeout=self._timeout(),
                    )
                except ApiException as exc:
                    if exc.status == 404:
                        return self._fail(
                            "no pods matched selector, nothing injected",
                            no_target=True,
                            matched_count=0,
                            pod=pod,
                            namespace=ns,
                        )
                    raise
                return self._ok(
                    message=f"deleted pod {pod}",
                    matched_count=1,
                    pod=pod,
                    namespace=ns,
                )
            # --- END MODIFICATION ---
        except ApiException as exc:
            return self._fail(str(exc))
        return self._fail("pod name or label_selector required")

    def count_pods(self, params: Dict[str, Any]) -> Dict[str, Any]:
        from kubernetes import client

        ns = params.get("namespace") or self.default_namespace
        label_selector = params.get("label_selector")
        if not label_selector:
            return self._fail("label_selector required")
        v1 = client.CoreV1Api(self._client())
        items = v1.list_namespaced_pod(
            ns,
            label_selector=_as_selector(label_selector),
            _request_timeout=self._timeout(),
        ).items
        return self._ok(count=len(items or []))

    def list_ephemeral(self, params: Dict[str, Any]) -> Dict[str, Any]:
        items, skipped = self._list_chaos(params)
        return self._ok(
            items=items,
            count=len(items),
            skipped_kinds=skipped,
            selector=_label_selector(
                params.get("managed_by") or self.managed_by,
                str(params.get("ephemeral") or self.ephemeral),
            ),
            message=f"{len(items)} ephemeral Chaos Mesh CR(s)",
        )

    def gc_ephemeral(self, params: Dict[str, Any]) -> Dict[str, Any]:
        items, skipped = self._list_chaos(params)
        selector = _label_selector(
            params.get("managed_by") or self.managed_by,
            str(params.get("ephemeral") or self.ephemeral),
        )
        if self.dry_run or params.get("list_only"):
            return self._ok(
                items=items,
                count=len(items),
                skipped_kinds=skipped,
                selector=selector,
                message=f"dry-run: would delete {len(items)} CR(s)",
            )
        deleted: List[str] = []
        errors: List[str] = []
        for item in items:
            kind, name, ns = item["kind"], item["name"], item["namespace"]
            try:
                self._delete_object(kind, name, ns, force=False, timeout=self._timeout())
                deleted.append(f"{ns}/{kind}/{name}")
            except Exception as exc:
                errors.append(f"{ns}/{kind}/{name}: {exc}")
        ok = not errors
        result = self._ok(
            items=items,
            deleted=deleted,
            errors=errors,
            count=len(deleted),
            skipped_kinds=skipped,
            selector=selector,
            message=f"deleted {len(deleted)} CR(s)"
            + (f"; {len(errors)} failed" if errors else ""),
        )
        if not ok:
            result["success"] = False
            result["error"] = "; ".join(errors)
        if not items and skipped == list(CHAOS_PLURALS):
            result["success"] = False
            result["error"] = "Chaos Mesh CRDs not found (install Chaos Mesh or use kubectl fallback)"
        return result

    def _list_chaos(self, params: Dict[str, Any]) -> tuple[List[Dict[str, str]], List[str]]:
        from kubernetes.client.rest import ApiException

        selector = _label_selector(
            params.get("managed_by") or self.managed_by,
            str(params.get("ephemeral") or self.ephemeral),
        )
        ns = params.get("namespace")
        kinds = params.get("kinds")
        plurals: List[str]
        if kinds:
            if isinstance(kinds, str):
                plurals = [p.strip() for p in kinds.split(",") if p.strip()]
            else:
                plurals = list(kinds)
        else:
            plurals = list(CHAOS_PLURALS)

        items: List[Dict[str, str]] = []
        skipped: List[str] = []
        custom = self._custom()
        kind_by_plural = {v: k for k, v in KIND_TO_PLURAL.items()}
        for plural in plurals:
            try:
                if ns:
                    payload = custom.list_namespaced_custom_object(
                        CHAOS_GROUP,
                        CHAOS_VERSION,
                        ns,
                        plural,
                        label_selector=selector,
                        _request_timeout=self._timeout(),
                    )
                else:
                    payload = custom.list_cluster_custom_object(
                        CHAOS_GROUP,
                        CHAOS_VERSION,
                        plural,
                        label_selector=selector,
                        _request_timeout=self._timeout(),
                    )
            except ApiException as exc:
                if exc.status in (404, 403):
                    skipped.append(plural)
                    continue
                raise
            for raw in payload.get("items") or []:
                meta = raw.get("metadata") or {}
                items.append(
                    {
                        "kind": kind_by_plural.get(plural, raw.get("kind") or plural),
                        "name": meta.get("name") or "",
                        "namespace": meta.get("namespace") or ns or "",
                    }
                )
        return items, skipped
