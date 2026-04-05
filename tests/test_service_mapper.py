"""
Tests for ServiceMapper — specifically the NetworkX serialization boundary (Blind Spot 1).

Validates:
  - nx.DiGraph is NEVER returned directly; only ServiceMap (Pydantic) is returned
  - node_link_data is always a plain dict (JSON-serializable)
  - nodes / edges are proper Pydantic model instances
  - JSON serialization of ServiceMap succeeds without TypeError
"""

from __future__ import annotations

import json

import networkx as nx
import pytest

from chaosgen.schemas.discovery import EnvironmentProfile, EnvironmentType, ServiceMap


class TestServiceMapperSerialization:
    """Core blind-spot test: the serialization boundary must never be crossed."""

    def _build_map_from_graph(self, graph: nx.DiGraph) -> ServiceMap:
        from chaosgen.discovery.service_mapper import ServiceMapper
        return ServiceMapper._graph_to_service_map(graph)

    def test_service_map_is_pydantic_not_digraph(self):
        g = nx.DiGraph()
        g.add_node("svc-a", name="svc-a", node_type="container", port=8080, namespace=None, labels={})
        result = self._build_map_from_graph(g)
        assert isinstance(result, ServiceMap)
        assert not isinstance(result, nx.DiGraph)

    def test_node_link_data_is_plain_dict(self):
        g = nx.DiGraph()
        g.add_node("svc-a", name="svc-a", node_type="container", port=None, namespace=None, labels={})
        result = self._build_map_from_graph(g)
        assert isinstance(result.node_link_data, dict)

    def test_json_serialization_succeeds(self):
        """node_link_data must not raise TypeError when passed to json.dumps."""
        g = nx.DiGraph()
        g.add_node("svc-a", name="svc-a", node_type="container", port=None, namespace=None, labels={})
        g.add_node("svc-b", name="svc-b", node_type="container", port=None, namespace=None, labels={})
        g.add_edge("svc-a", "svc-b", protocol="http")
        result = self._build_map_from_graph(g)

        # This must not raise TypeError
        json_str = result.model_dump_json()
        parsed = json.loads(json_str)
        assert "nodes" in parsed
        assert "edges" in parsed
        assert "node_link_data" in parsed

    def test_nodes_are_service_node_instances(self):
        from chaosgen.schemas.discovery import ServiceNode
        g = nx.DiGraph()
        g.add_node("svc-a", name="svc-a", node_type="service", port=8080, namespace="default", labels={"app": "x"})
        result = self._build_map_from_graph(g)
        assert len(result.nodes) == 1
        assert isinstance(result.nodes[0], ServiceNode)
        assert result.nodes[0].name == "svc-a"
        assert result.nodes[0].port == 8080

    def test_edges_are_service_edge_instances(self):
        from chaosgen.schemas.discovery import ServiceEdge
        g = nx.DiGraph()
        g.add_node("a", name="a", node_type="s", port=None, namespace=None, labels={})
        g.add_node("b", name="b", node_type="s", port=None, namespace=None, labels={})
        g.add_edge("a", "b", protocol="grpc")
        result = self._build_map_from_graph(g)
        assert len(result.edges) == 1
        assert isinstance(result.edges[0], ServiceEdge)
        assert result.edges[0].protocol == "grpc"

    def test_critical_paths_computed_correctly(self):
        g = nx.DiGraph()
        for n in ("a", "b", "c"):
            g.add_node(n, name=n, node_type="s", port=None, namespace=None, labels={})
        g.add_edge("a", "b", protocol="http")
        g.add_edge("b", "c", protocol="http")
        result = self._build_map_from_graph(g)
        assert ["a", "b", "c"] in result.critical_paths

    def test_empty_graph_returns_empty_service_map(self):
        result = self._build_map_from_graph(nx.DiGraph())
        assert result.nodes == []
        assert result.edges == []
        assert isinstance(result.node_link_data, dict)


class TestServiceMapperDockerCompose:
    def test_docker_compose_build(self, tmp_path, monkeypatch):
        import yaml
        from chaosgen.discovery.service_mapper import ServiceMapper

        monkeypatch.chdir(tmp_path)
        compose = {
            "services": {
                "web":  {"image": "nginx", "ports": ["80:80"]},
                "db":   {"image": "postgres", "ports": ["5432:5432"]},
                "cache": {"image": "redis", "depends_on": ["db"]},
            }
        }
        (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))

        env = EnvironmentProfile(type=EnvironmentType.DOCKER_COMPOSE)
        mapper = ServiceMapper(env_profile=env)
        result = mapper.build()

        assert isinstance(result, ServiceMap)
        assert len(result.nodes) == 3
        node_names = {n.name for n in result.nodes}
        assert {"web", "db", "cache"} == node_names

        # Serialization must still succeed
        json.loads(result.model_dump_json())
