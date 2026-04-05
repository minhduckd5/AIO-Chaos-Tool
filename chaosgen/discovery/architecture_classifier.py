"""
Architecture Classifier — infers software architecture type from infrastructure signals.

Heuristic-based classification; confidence is reported alongside the result.
A human can override the detected type via CLI (--arch) or GUI settings.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from chaosgen.schemas.discovery import ArchitectureProfile, ArchitectureType, EnvironmentProfile, EnvironmentType

logger = logging.getLogger(__name__)

_MESSAGE_BROKER_NAMES = frozenset({"kafka", "rabbitmq", "nats", "pulsar", "activemq", "redpanda"})
_API_GATEWAY_NAMES = frozenset({"kong", "nginx", "traefik", "envoy", "ambassador", "apigee"})
_MICROSERVICE_NS_THRESHOLD = 3     # K8s: ≥3 app namespaces → likely microservices
_MICROSERVICE_SVC_THRESHOLD = 5    # K8s: ≥5 services → likely microservices


class ArchitectureClassifier:
    def __init__(
        self,
        env_profile: EnvironmentProfile,
        compose_file: str | None = None,
        kubeconfig: str | None = None,
    ) -> None:
        self._env = env_profile
        self._compose_file = compose_file
        self._kubeconfig = kubeconfig

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, user_hint: ArchitectureType | None = None) -> ArchitectureProfile:
        auto = self._auto_classify()

        if user_hint is not None and user_hint != auto.type:
            auto.signals.append(
                f"USER_HEURISTIC_MISMATCH: user specified '{user_hint.value}' "
                f"but auto-detection inferred '{auto.type.value}'"
            )
            auto.type = user_hint
            auto.confidence = min(auto.confidence, 0.90)
        elif user_hint is not None:
            auto.signals.append(f"User hint '{user_hint.value}' confirmed by auto-detection")
            auto.confidence = min(auto.confidence + 0.15, 1.0)

        return auto

    def _auto_classify(self) -> ArchitectureProfile:
        if self._env.type == EnvironmentType.KUBERNETES:
            return self._classify_kubernetes()
        if self._env.type == EnvironmentType.DOCKER_COMPOSE:
            return self._classify_docker_compose()
        return ArchitectureProfile(
            type=ArchitectureType.UNKNOWN,
            confidence=0.0,
            signals=["Environment type not supported for automatic classification"],
        )

    # ------------------------------------------------------------------
    # Kubernetes classification
    # ------------------------------------------------------------------

    def _classify_kubernetes(self) -> ArchitectureProfile:
        signals: list[str] = []
        has_broker = False
        has_gateway = False
        service_count = 0

        try:
            from kubernetes import client, config as k8s_config  # type: ignore
            from pathlib import Path as _Path
            _SA_TOKEN = _Path("/var/run/secrets/kubernetes.io/serviceaccount/token")

            if _SA_TOKEN.exists():
                k8s_config.load_incluster_config()
            else:
                k8s_config.load_kube_config(config_file=self._kubeconfig)

            v1 = client.CoreV1Api()
            apps_v1 = client.AppsV1Api()

            namespaces = [
                ns.metadata.name for ns in v1.list_namespace().items
                if ns.metadata.name not in ("kube-system", "kube-public", "kube-node-lease")
            ]
            services = v1.list_service_for_all_namespaces().items
            service_count = len(services)

            for svc in services:
                name = (svc.metadata.name or "").lower()
                if any(broker in name for broker in _MESSAGE_BROKER_NAMES):
                    has_broker = True
                    signals.append(f"Message broker detected: {svc.metadata.name}")
                if any(gw in name for gw in _API_GATEWAY_NAMES):
                    has_gateway = True
                    signals.append(f"API gateway detected: {svc.metadata.name}")

            deployments = apps_v1.list_deployment_for_all_namespaces().items

        except Exception as exc:
            logger.warning("K8s API unavailable for architecture classification: %s", exc)
            return ArchitectureProfile(
                type=ArchitectureType.UNKNOWN,
                confidence=0.2,
                signals=[f"K8s API error: {exc}"],
            )

        # --- scoring ---
        if has_broker:
            signals.append("Event-driven signal: message broker present")
            arch_type = ArchitectureType.EVENT_DRIVEN
            confidence = 0.80
        elif service_count >= _MICROSERVICE_SVC_THRESHOLD or len(namespaces) >= _MICROSERVICE_NS_THRESHOLD:
            signals.append(f"Microservices signal: {service_count} services across {len(namespaces)} namespaces")
            arch_type = ArchitectureType.MICROSERVICES
            confidence = 0.75
        elif service_count <= 2:
            signals.append(f"Monolith signal: only {service_count} services detected")
            arch_type = ArchitectureType.MONOLITH
            confidence = 0.65
        else:
            arch_type = ArchitectureType.MODULAR_MONOLITH
            confidence = 0.55
            signals.append("Modular monolith inferred by elimination")

        if self._env.has_service_mesh:
            signals.append("Service mesh detected — reinforces microservices/EDA classification")
            confidence = min(confidence + 0.10, 1.0)

        return ArchitectureProfile(
            type=arch_type,
            service_count=service_count,
            has_message_broker=has_broker,
            has_api_gateway=has_gateway,
            has_service_mesh=self._env.has_service_mesh,
            confidence=round(confidence, 2),
            signals=signals,
        )

    # ------------------------------------------------------------------
    # Docker Compose classification
    # ------------------------------------------------------------------

    def _classify_docker_compose(self) -> ArchitectureProfile:
        signals: list[str] = []
        has_broker = False
        has_gateway = False
        service_count = 0

        compose_path = self._compose_file
        if compose_path is None:
            for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
                if Path(name).exists():
                    compose_path = name
                    break

        if compose_path is None or not Path(compose_path).exists():
            return ArchitectureProfile(
                type=ArchitectureType.UNKNOWN,
                confidence=0.1,
                signals=["docker-compose.yml not found"],
            )

        try:
            with open(compose_path, encoding="utf-8") as f:
                compose = yaml.safe_load(f)
        except Exception as exc:
            return ArchitectureProfile(
                type=ArchitectureType.UNKNOWN,
                confidence=0.0,
                signals=[f"Failed to parse compose file: {exc}"],
            )

        services: dict = compose.get("services", {})
        service_count = len(services)

        for svc_name, svc_def in services.items():
            name_lower = svc_name.lower()
            image = (svc_def.get("image") or "").lower()

            if any(broker in name_lower or broker in image for broker in _MESSAGE_BROKER_NAMES):
                has_broker = True
                signals.append(f"Message broker detected: {svc_name}")
            if any(gw in name_lower or gw in image for gw in _API_GATEWAY_NAMES):
                has_gateway = True
                signals.append(f"API gateway detected: {svc_name}")

        # depth of depends_on graph as a proxy for coupling
        max_depth = self._compute_depends_on_depth(services)
        signals.append(f"docker-compose: {service_count} services, depends_on depth={max_depth}")

        if has_broker:
            arch_type = ArchitectureType.EVENT_DRIVEN
            confidence = 0.78
        elif service_count >= 5:
            arch_type = ArchitectureType.MICROSERVICES
            confidence = 0.70
        elif service_count == 2:
            arch_type = ArchitectureType.CLIENT_SERVER
            confidence = 0.72
            signals.append("Client-server signal: exactly 2 services")
        elif service_count == 1:
            arch_type = ArchitectureType.MONOLITH
            confidence = 0.80
        else:
            arch_type = ArchitectureType.MODULAR_MONOLITH
            confidence = 0.55

        return ArchitectureProfile(
            type=arch_type,
            service_count=service_count,
            has_message_broker=has_broker,
            has_api_gateway=has_gateway,
            confidence=round(confidence, 2),
            signals=signals,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_depends_on_depth(services: dict) -> int:
        """BFS over depends_on to find max dependency chain depth."""
        depth: dict[str, int] = {name: 0 for name in services}
        changed = True
        while changed:
            changed = False
            for name, svc_def in services.items():
                deps = svc_def.get("depends_on", [])
                if isinstance(deps, dict):
                    deps = list(deps.keys())
                for dep in deps:
                    if dep in depth and depth[dep] + 1 > depth[name]:
                        depth[name] = depth[dep] + 1
                        changed = True
        return max(depth.values(), default=0)
