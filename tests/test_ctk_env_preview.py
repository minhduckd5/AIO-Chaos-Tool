"""Environment-aware CTK preview: P1 must not emit chaosk8s selectors."""

from __future__ import annotations

import json

from chaosgen.gui.ctk_form import (
    build_ctk_experiment_for_environment,
    experiment_to_json,
)


def _row(ftype: str = "process_kill", services: list[str] | None = None) -> dict:
    return {
        "ftype": ftype,
        "duration": "30s",
        "latency": "2000ms",
        "loss_percentage": 10.0,
        "signal": "SIGKILL",
        "services": services or ["frontend"],
    }


def _dump(env: str, fault_rows: list[dict] | None = None, **kwargs) -> dict:
    exp = build_ctk_experiment_for_environment(
        environment=env,
        title=f"{env}-preview",
        description="compat check",
        fault_rows=fault_rows if fault_rows is not None else [_row()],
        **kwargs,
    )
    return json.loads(experiment_to_json(exp))


def test_kubernetes_preview_still_uses_chaosk8s():
    data = _dump("kubernetes")
    blob = json.dumps(data)
    assert "chaosk8s" in blob
    assert data["method"][0]["provider"]["type"] == "python"


def test_docker_compose_preview_uses_pumba_process():
    data = _dump("docker_compose", fault_rows=[_row(services=["monolith-app"])])
    provider = data["method"][0]["provider"]
    assert provider["type"] == "process"
    assert provider["path"] == "pumba"
    blob = json.dumps(data)
    assert "chaosk8s" not in blob
    assert "label_selector" not in blob


def test_bare_metal_preview_uses_process_not_k8s():
    data = _dump(
        "bare_metal",
        fault_rows=[_row("resource_exhaustion")],
        host_target="10.0.0.12",
        host_ssh_user="deploy",
        host_ssh_port=22,
    )
    provider = data["method"][0]["provider"]
    assert provider["type"] == "process"
    assert provider["path"] == "stress-ng"
    blob = json.dumps(data)
    assert "chaosk8s" not in blob
    assert "label_selector" not in blob


def test_serverless_preview_uses_http_boundary_not_k8s():
    data = _dump(
        "serverless",
        fault_rows=[_row("network_latency", services=[])],
        faas_provider="aws_lambda",
        faas_function="arn:aws:lambda:us-east-1:123:function:orders",
    )
    provider = data["method"][0]["provider"]
    assert provider["type"] == "http"
    assert "arn:aws:lambda" in provider["url"] or "orders" in provider["url"]
    blob = json.dumps(data)
    assert "chaosk8s" not in blob
    assert "terminate_pods" not in blob
    assert "label_selector" not in blob
    assert "env:serverless" in data.get("tags", [])


def test_serverless_prefers_function_arn_over_fault_row_service():
    data = _dump(
        "serverless",
        fault_rows=[_row(services=["frontend"])],
        faas_function="my-cold-start-fn",
    )
    assert "my-cold-start-fn" in json.dumps(data)
