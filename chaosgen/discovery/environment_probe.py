"""
Environment Probe — detects the deployment environment (K8s, Docker, Cloud VM, Bare Metal).

Probe order (first match wins):
  1. Kubernetes  — kubeconfig file or in-cluster service account
  2. Docker      — Docker socket or docker-compose.yml presence
  3. Cloud VM    — IMDS metadata endpoint (AWS/GCP/Azure)
  4. Bare Metal  — fallback
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

import requests

from chaosgen.schemas.discovery import EnvironmentProfile, EnvironmentType

logger = logging.getLogger(__name__)

_IMDS_URL = "http://169.254.169.254/"
_IMDS_TIMEOUT = 1.0          # seconds — must be short to avoid stalling on bare metal
_DOCKER_SOCKET = Path("/var/run/docker.sock")
_K8S_SA_TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


class EnvironmentProbe:
    def __init__(self, kubeconfig: str | None = None) -> None:
        self._kubeconfig = kubeconfig or os.environ.get(
            "KUBECONFIG", str(Path.home() / ".kube" / "config")
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def probe(self) -> EnvironmentProfile:
        if self._is_kubernetes():
            return self._build_kubernetes_profile()
        if self._is_docker():
            return EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE)
        cloud = self._detect_cloud_vm()
        if cloud:
            return EnvironmentProfile(type=EnvironmentType.CLOUD_VM, cloud_provider=cloud)
        return EnvironmentProfile(type=EnvironmentType.BARE_METAL)

    # ------------------------------------------------------------------
    # Kubernetes detection
    # ------------------------------------------------------------------

    def _is_kubernetes(self) -> bool:
        # In-cluster service account
        if _K8S_SA_TOKEN.exists():
            return True
        # kubeconfig file
        if Path(self._kubeconfig).exists():
            return True
        return False

    def _build_kubernetes_profile(self) -> EnvironmentProfile:
        profile = EnvironmentProfile(type=EnvironmentType.KUBERNETES)
        try:
            from kubernetes import client, config as k8s_config  # type: ignore

            if _K8S_SA_TOKEN.exists():
                k8s_config.load_incluster_config()
            else:
                k8s_config.load_kube_config(config_file=self._kubeconfig)

            v1 = client.CoreV1Api()
            version_api = client.VersionApi()

            nodes = v1.list_node()
            namespaces = v1.list_namespace()
            ver = version_api.get_code()

            profile.node_count = len(nodes.items)
            profile.namespace_count = len(namespaces.items)
            profile.runtime_version = f"{ver.major}.{ver.minor}"

            # Detect service mesh via Istio label on namespaces
            for ns in namespaces.items:
                labels = ns.metadata.labels or {}
                if "istio-injection" in labels or "linkerd.io/inject" in labels:
                    profile.has_service_mesh = True
                    break

        except Exception as exc:
            logger.warning("Could not enrich K8s profile: %s", exc)

        return profile

    # ------------------------------------------------------------------
    # Docker detection
    # ------------------------------------------------------------------

    def _is_docker(self) -> bool:
        if _DOCKER_SOCKET.exists():
            return True
        # Check for docker-compose.yml in cwd
        for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
            if Path(name).exists():
                return True
        return False

    # ------------------------------------------------------------------
    # Cloud VM detection (IMDS)
    # ------------------------------------------------------------------

    def _detect_cloud_vm(self) -> str | None:
        """
        Ping the Instance Metadata Service endpoint common to all major clouds.
        Returns provider name or None. Uses a very short timeout so bare metal
        hosts are not delayed.
        """
        try:
            resp = requests.get(_IMDS_URL, timeout=_IMDS_TIMEOUT)
            server = resp.headers.get("Server", "")
            # AWS returns EC2 metadata service headers
            if "EC2" in resp.text or "amazonaws" in server.lower():
                return "aws"
            # GCP returns specific metadata flavor
            if "Google" in server:
                return "gcp"
            return "cloud_vm"
        except (requests.ConnectionError, requests.Timeout, socket.timeout):
            pass

        # Azure uses a different metadata endpoint
        try:
            resp = requests.get(
                "http://169.254.169.254/metadata/instance",
                headers={"Metadata": "true"},
                timeout=_IMDS_TIMEOUT,
            )
            if resp.status_code == 200:
                return "azure"
        except (requests.ConnectionError, requests.Timeout):
            pass

        return None
