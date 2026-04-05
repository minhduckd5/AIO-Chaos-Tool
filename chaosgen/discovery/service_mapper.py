"""
Service Mapper — builds a directed service dependency graph and exports it
as a JSON-serializable ServiceMap.

CRITICAL (Blind Spot 1): nx.DiGraph is used ONLY internally for graph
algorithms. The public API returns a ServiceMap (Pydantic model) with
node_link_data pre-serialized via nx.node_link_data(). Raw DiGraph objects
never leave this module.
"""

from __future__ import annotations

import logging
from pathlib import Path

import networkx as nx
import yaml

from chaosgen.schemas.discovery import (
    EnvironmentProfile,
    EnvironmentType,
    ServiceEdge,
    ServiceMap,
    ServiceNode,
)

logger = logging.getLogger(__name__)


class ServiceMapper:
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

    def build(self) -> ServiceMap:
        """Build and return a JSON-serializable ServiceMap."""
        if self._env.type == EnvironmentType.KUBERNETES:
            graph = self._build_from_kubernetes()
        elif self._env.type == EnvironmentType.DOCKER_COMPOSE:
            graph = self._build_from_docker_compose()
        else:
            logger.info("ServiceMapper: unsupported environment %s, returning empty map", self._env.type)
            graph = nx.DiGraph()

        return self._graph_to_service_map(graph)

    # ------------------------------------------------------------------
    # Kubernetes source
    # ------------------------------------------------------------------

    def _build_from_kubernetes(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        try:
            from kubernetes import client, config as k8s_config  # type: ignore
            from pathlib import Path as _Path
            _SA_TOKEN = _Path("/var/run/secrets/kubernetes.io/serviceaccount/token")

            if _SA_TOKEN.exists():
                k8s_config.load_incluster_config()
            else:
                k8s_config.load_kube_config(config_file=self._kubeconfig)

            v1 = client.CoreV1Api()
            services = v1.list_service_for_all_namespaces().items

            for svc in services:
                node_id = f"{svc.metadata.namespace}/{svc.metadata.name}"
                port = None
                if svc.spec.ports:
                    port = svc.spec.ports[0].port
                graph.add_node(
                    node_id,
                    name=svc.metadata.name,
                    node_type="Service",
                    port=port,
                    namespace=svc.metadata.namespace,
                    labels=svc.metadata.labels or {},
                )

            # Infer edges from Istio VirtualService CRDs if available
            try:
                custom_api = client.CustomObjectsApi()
                virtual_services = custom_api.list_cluster_custom_object(
                    group="networking.istio.io",
                    version="v1beta1",
                    plural="virtualservices",
                )
                for vs in virtual_services.get("items", []):
                    ns = vs["metadata"]["namespace"]
                    host = vs["spec"].get("hosts", [None])[0]
                    for http_route in vs["spec"].get("http", []):
                        for dest in http_route.get("route", []):
                            dest_host = dest["destination"]["host"]
                            source_id = f"{ns}/{host}"
                            target_id = f"{ns}/{dest_host}"
                            if graph.has_node(source_id) and graph.has_node(target_id):
                                graph.add_edge(source_id, target_id, protocol="http")
            except Exception as exc:
                logger.debug("Istio VirtualService enumeration skipped: %s", exc)

        except Exception as exc:
            logger.warning("ServiceMapper K8s build failed: %s", exc)

        return graph

    # ------------------------------------------------------------------
    # Docker Compose source
    # ------------------------------------------------------------------

    def _build_from_docker_compose(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        compose_path = self._compose_file
        if compose_path is None:
            for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
                if Path(name).exists():
                    compose_path = name
                    break
        if compose_path is None:
            return graph

        try:
            with open(compose_path, encoding="utf-8") as f:
                compose = yaml.safe_load(f)
        except Exception as exc:
            logger.warning("ServiceMapper: failed to parse compose file: %s", exc)
            return graph

        services: dict = compose.get("services", {})
        for name, svc_def in services.items():
            ports = svc_def.get("ports", [])
            port: int | None = None
            if ports:
                raw = str(ports[0]).split(":")[-1]
                try:
                    port = int(raw)
                except ValueError:
                    pass
            graph.add_node(
                name,
                name=name,
                node_type="container",
                port=port,
                namespace=None,
                labels={},
            )

        for name, svc_def in services.items():
            deps = svc_def.get("depends_on", [])
            if isinstance(deps, dict):
                deps = list(deps.keys())
            for dep in deps:
                if dep in services:
                    graph.add_edge(dep, name, protocol="internal")

        return graph

    # ------------------------------------------------------------------
    # Serialization boundary (Blind Spot 1 fix)
    # ------------------------------------------------------------------

    @staticmethod
    def _graph_to_service_map(graph: nx.DiGraph) -> ServiceMap:
        """
        Convert a nx.DiGraph to a fully JSON-serializable ServiceMap.
        This is the ONLY exit point for graph data from this module.
        """
        nodes = [
            ServiceNode(
                id=node_id,
                name=attrs.get("name", node_id),
                node_type=attrs.get("node_type", "service"),
                port=attrs.get("port"),
                namespace=attrs.get("namespace"),
                labels=attrs.get("labels", {}),
            )
            for node_id, attrs in graph.nodes(data=True)
        ]

        edges = [
            ServiceEdge(
                source=u,
                target=v,
                protocol=attrs.get("protocol"),
            )
            for u, v, attrs in graph.edges(data=True)
        ]

        # Identify critical paths (longest simple paths between leaf pairs)
        critical_paths: list[list[str]] = []
        try:
            sources = [n for n in graph.nodes if graph.in_degree(n) == 0]
            sinks = [n for n in graph.nodes if graph.out_degree(n) == 0]
            for src in sources[:3]:
                for snk in sinks[:3]:
                    try:
                        path = nx.shortest_path(graph, src, snk)
                        if len(path) > 1:
                            critical_paths.append(path)
                    except nx.NetworkXNoPath:
                        pass
        except Exception:
            pass

        return ServiceMap(
            nodes=nodes,
            edges=edges,
            node_link_data=nx.node_link_data(graph),   # JSON-safe dict
            critical_paths=critical_paths,
        )
