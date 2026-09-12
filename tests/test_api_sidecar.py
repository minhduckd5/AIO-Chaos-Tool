"""Phase 1 & Phase 2A FastAPI sidecar — honesty, auth, lock, anti-reapprove, catalog, settings, experiments."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from chaosgen.api.app import create_app
from chaosgen.orchestrator import ChaosOrchestrator
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    ProcessFaultSpec,
    TargetSpec,
    TargetType,
)
from chaosgen.schemas.scenarios import AdvisorReport
from chaosgen.storage.atomic_io import append_jsonl_line
from chaosgen.storage.audit import AuditStore


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


def _make_experiment(name: str = "api-exp-0", namespace: str = "default") -> ChaosExperiment:
    return ChaosExperiment(
        name=name,
        target=TargetSpec(type=TargetType.SERVICE, name="test-svc", namespace=namespace),
        faults=[
            ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="10s"),
        ],
    )


def _stub_fast_inject(orch: ChaosOrchestrator) -> None:
    orch.validator.validate = lambda _c: True
    orch._start_dead_mans_switch = lambda: None

    def _fast_inject():
        orch.last_outcome = "PASS"
        orch.injection_complete()

    def _fast_verify():
        orch.verification_complete()

    orch._execute_injection = _fast_inject
    orch._run_verification = _fast_verify


@pytest.fixture
def orch() -> ChaosOrchestrator:
    return ChaosOrchestrator()


@pytest.fixture
def client(orch: ChaosOrchestrator) -> TestClient:
    app = create_app(orchestrator=orch, mutating_lock=threading.Lock())
    with TestClient(app) as test_client:
        yield test_client


class TestApiHealthAndAuth:
    def test_health_ok(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_spa_static_serving(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "ChaosGen" in resp.text

    def test_mutating_requires_operator_header(self, client: TestClient):
        for path in (
            "/v1/pending/0/approve",
            "/v1/pending/reject-all",
            "/v1/halt",
            "/v1/catalog/dns-resolution-failure/queue",
            "/v1/experiments/run",
            "/v1/settings",
            "/v1/control/orphan-sweep",
            "/v1/telemetry/analyze",
        ):
            resp = client.post(path) if path != "/v1/settings" else client.put(path, json={})
            assert resp.status_code == 401, path


class TestApiApproveHonesty:
    def test_approve_empty_pending(self, client: TestClient, orch: ChaosOrchestrator):
        resp = client.post(
            "/v1/pending/0/approve",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ran") is False
        assert "no pending" in str(body.get("reason") or "")

    def test_approve_invalid_index(self, client: TestClient, orch: ChaosOrchestrator):
        orch.run_ai_experiment(
            AdvisorReport(
                anomalies_found=1,
                generated_experiments=[_make_experiment()],
            )
        )
        resp = client.post(
            "/v1/pending/99/approve",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ran") is False
        assert "invalid experiment index" in str(body.get("reason") or "")

    def test_approve_sets_api_hitl_and_runs(self, client: TestClient, orch: ChaosOrchestrator):
        orch.run_ai_experiment(
            AdvisorReport(
                anomalies_found=1,
                generated_experiments=[_make_experiment("hitl-ok")],
            )
        )
        _stub_fast_inject(orch)
        resp = client.post(
            "/v1/pending/0/approve",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ran") is True
        assert orch._audit_path_override == "api_hitl"
        assert orch._audit_actor == "api-tester"

    def test_concurrent_double_approve_one_winner(self, orch: ChaosOrchestrator):
        orch.run_ai_experiment(
            AdvisorReport(
                anomalies_found=1,
                generated_experiments=[_make_experiment("once-only")],
            )
        )
        _stub_fast_inject(orch)
        app = create_app(orchestrator=orch, mutating_lock=threading.Lock())
        barrier = threading.Barrier(2)

        def _approve() -> dict:
            with TestClient(app) as c:
                barrier.wait(timeout=5)
                r = c.post(
                    "/v1/pending/0/approve",
                    headers={"X-Operator-Name": "api-tester"},
                )
                assert r.status_code == 200
                return r.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_approve), pool.submit(_approve)]
            results = [f.result(timeout=60) for f in futures]

        winners = [r for r in results if r.get("ran") is True]
        losers = [r for r in results if r.get("ran") is False]
        assert len(winners) == 1
        assert len(losers) == 1
        assert "already approved this queue" in str(losers[0].get("reason") or "")


class TestApiRejectHalt:
    def test_reject_all(self, client: TestClient, orch: ChaosOrchestrator):
        orch.run_ai_experiment(
            AdvisorReport(
                anomalies_found=2,
                generated_experiments=[
                    _make_experiment("a"),
                    _make_experiment("b"),
                ],
            )
        )
        resp = client.post(
            "/v1/pending/reject-all",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 200
        assert resp.json().get("ok") is True
        assert resp.json().get("state") == "idle"
        assert orch.get_pending_experiments() == []

    def test_reject_single_post_and_delete(self, client: TestClient, orch: ChaosOrchestrator):
        orch.run_ai_experiment(
            AdvisorReport(
                anomalies_found=2,
                generated_experiments=[
                    _make_experiment("exp-1"),
                    _make_experiment("exp-2"),
                ],
            )
        )
        # Auth check
        unauth = client.post("/v1/pending/0/reject")
        assert unauth.status_code == 401
        unauth_del = client.delete("/v1/pending/0")
        assert unauth_del.status_code == 401

        # Reject index 0 via POST
        resp = client.post(
            "/v1/pending/0/reject",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("remaining") == 1
        assert data.get("state") == "pending_approval"

        # Reject remaining via DELETE
        resp_del = client.delete(
            "/v1/pending/0",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp_del.status_code == 200
        data_del = resp_del.json()
        assert data_del.get("ok") is True
        assert data_del.get("remaining") == 0
        assert data_del.get("state") == "idle"

        # Reject from empty queue
        resp_empty = client.post(
            "/v1/pending/0/reject",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp_empty.status_code == 200
        assert resp_empty.json().get("ok") is False

    def test_halt(self, client: TestClient, orch: ChaosOrchestrator):
        resp = client.post(
            "/v1/halt",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 200
        assert "ctk_aborted" in resp.json()


class TestApiAuditVerdict:
    def test_audit_recent_skips_corrupt_tail(self, client: TestClient, tmp_path: Path, monkeypatch):
        path = tmp_path / "audit.jsonl"
        append_jsonl_line(path, {"schema_version": 1, "event_id": "00000000-0000-4000-8000-000000000001",
                                 "timestamp": "2026-01-01T00:00:00Z", "actor": "t",
                                 "event_type": "queued", "path_used": "ai_hitl"})
        path.write_text(path.read_text(encoding="utf-8") + "{not-json\n", encoding="utf-8")
        monkeypatch.setenv("CHAOSGEN_AUDIT_LOG", str(path))
        resp = client.get("/v1/audit/recent")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    def test_verdict_last_404(self, client: TestClient, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            "chaosgen.api.routes.verdict.load_verdict_report",
            lambda path=None: (_ for _ in ()).throw(FileNotFoundError("No expectation verdict")),
        )
        resp = client.get("/v1/verdict/last")
        assert resp.status_code == 404

    def test_audit_summary_aggregation(self, client: TestClient, tmp_path: Path, monkeypatch):
        path = tmp_path / "audit_summary.jsonl"
        store = AuditStore(path=path)
        store.emit(event_type="queued", actor="tester", path_used="ai_hitl")
        store.emit(event_type="inject_finished", actor="tester", path_used="ai_hitl", outcome="success")
        store.emit(event_type="rollback", actor="tester", path_used="ai_hitl", outcome="failure")
        store.emit(event_type="inject_finished", actor="tester", path_used="ai_hitl", outcome="aborted")

        monkeypatch.setenv("CHAOSGEN_AUDIT_LOG", str(path))
        resp = client.get("/v1/audit/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        assert data["pass"] == 1
        assert data["fail"] == 1


class TestApiPendingEnrichment:
    def test_pending_enriched_fields(self, client: TestClient, orch: ChaosOrchestrator):
        exp = _make_experiment("enriched-exp")
        exp.description = "Downstream service recovers under retry"
        orch.run_ai_experiment(
            AdvisorReport(
                anomalies_found=1,
                generated_experiments=[exp],
            )
        )
        resp = client.get("/v1/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        item = data["pending"][0]
        assert item["name"] == "enriched-exp"
        assert item["origin"] == "ai_advisor"
        assert item["hypothesis"] == "Downstream service recovers under retry"
        assert item["target"] == "test-svc"
        assert item["consumed"] is False


class TestApiCatalogRoutes:
    def test_catalog_list(self, client: TestClient):
        resp = client.get("/v1/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] > 0
        names = [s["name"] for s in data["scenarios"]]
        assert any("DNS resolution failure" in n for n in names)

    def test_catalog_queue_404(self, client: TestClient):
        resp = client.post(
            "/v1/catalog/non-existent-scenario-xyz/queue",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 404

    def test_catalog_queue_success_and_409_guard(self, client: TestClient, orch: ChaosOrchestrator):
        # 1. Queue first scenario
        resp1 = client.post(
            "/v1/catalog/dns-resolution-failure/queue",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["queued"] is True
        assert len(orch.pending_experiments) == 1

        # 2. Attempt queue second without force -> 409 Conflict
        resp2 = client.post(
            "/v1/catalog/dns-resolution-failure/queue",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp2.status_code == 409
        assert "unapproved scenario(s)" in resp2.json()["detail"]

        # 3. Queue with ?force=true -> replaces queue
        resp3 = client.post(
            "/v1/catalog/dns-resolution-failure/queue?force=true",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp3.status_code == 200
        assert resp3.json()["replaced_unapproved_count"] == 1


class TestApiExperimentsRoutes:
    def test_experiments_history(self, client: TestClient, tmp_path: Path, monkeypatch):
        from chaosgen.storage.audit import TargetClusterContext
        path = tmp_path / "exp_hist_audit.jsonl"
        store = AuditStore(path=path)
        ctx = TargetClusterContext(kube_context="minikube", kube_namespace="default")
        store.emit(event_type="inject_started", actor="tester", path_used="operator_direct", run_id="run-123", experiment_name="exp-1", target_cluster_context=ctx)
        store.emit(event_type="inject_finished", actor="tester", path_used="operator_direct", run_id="run-123", outcome="success", target_cluster_context=ctx)

        monkeypatch.setenv("CHAOSGEN_AUDIT_LOG", str(path))
        resp = client.get("/v1/experiments/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["runs"][0]["run_id"] == "run-123"
        assert data["runs"][0]["outcome"] == "PASS"
        assert data["runs"][0]["target_namespace"] == "default"

    def test_experiments_run_409_when_not_idle(self, client: TestClient, orch: ChaosOrchestrator):
        orch.state = "injecting"
        payload = _make_experiment("direct-run").model_dump(mode="json")
        resp = client.post(
            "/v1/experiments/run",
            json=payload,
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 409
        assert "not idle" in resp.json()["detail"]

    def test_experiments_run_422_blast_radius(self, client: TestClient, orch: ChaosOrchestrator):
        # Target in blocked namespace kube-system
        exp = _make_experiment("unsafe-exp", namespace="kube-system")
        payload = exp.model_dump(mode="json")
        resp = client.post(
            "/v1/experiments/run",
            json=payload,
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 422
        assert "Blast radius validation failed" in resp.json()["detail"]

    def test_experiments_run_success(self, client: TestClient, orch: ChaosOrchestrator):
        _stub_fast_inject(orch)
        exp = _make_experiment("safe-direct-exp")
        payload = exp.model_dump(mode="json")
        resp = client.post(
            "/v1/experiments/run",
            json=payload,
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        assert orch._audit_path == "operator_direct"


class TestApiSettingsRoutes:
    def test_settings_get_masked(self, client: TestClient):
        resp = client.get("/v1/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "settings" in data

    def test_settings_put_409_when_active(self, client: TestClient, orch: ChaosOrchestrator):
        orch.state = "injecting"
        resp = client.put(
            "/v1/settings",
            json={"developer_mode": True},
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 409

    def test_settings_put_success(self, client: TestClient, orch: ChaosOrchestrator, tmp_path: Path, monkeypatch):
        settings_file = tmp_path / "settings.yaml"
        monkeypatch.setattr("chaosgen.config.settings.SETTINGS_FILE", settings_file)
        resp = client.put(
            "/v1/settings",
            json={"developer_mode": True},
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 200
        assert resp.json()["saved"] is True
        # --- START MODIFICATION ---
        # Assert settings mutation sets api_config, never api_hitl
        assert orch._audit_actor == "api-tester"
        assert orch._audit_path_override == "api_config"
        assert orch._audit_path_override != "api_hitl"
        # --- END MODIFICATION ---


class TestApiControlOrphanSweep:
    def test_orphan_sweep(self, client: TestClient, orch: ChaosOrchestrator):
        resp = client.post(
            "/v1/control/orphan-sweep?older_than_seconds=0",
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 200
        assert resp.json()["swept"] is True


class TestApiTelemetryRoutes:
    def test_telemetry_ready(self, client: TestClient):
        resp = client.get("/v1/telemetry/ready")
        assert resp.status_code == 200
        assert "ready" in resp.json()

    def test_telemetry_check(self, client: TestClient):
        resp = client.post(
            "/v1/telemetry/check",
            json={"prom_url": "http://127.0.0.1:9090", "loki_url": "http://127.0.0.1:3100"},
        )
        assert resp.status_code == 200
        assert "prometheus" in resp.json()
        assert "loki" in resp.json()

    def test_telemetry_analyze_409_guard(self, client: TestClient, orch: ChaosOrchestrator):
        orch.pending_experiments = [_make_experiment("unapproved-1")]
        resp = client.post(
            "/v1/telemetry/analyze",
            json={"lookback_hours": 1},
            headers={"X-Operator-Name": "api-tester"},
        )
        assert resp.status_code == 409
        assert "unapproved scenario(s)" in resp.json()["detail"]


class TestApiEventsStream:
    @pytest.mark.anyio
    async def test_events_stream_handshake(self, orch: ChaosOrchestrator):
        from chaosgen.api.routes.events import event_stream
        resp = await event_stream(orch=orch)
        assert resp.media_type == "text/event-stream"
        gen = resp.body_iterator
        first_chunk = await anext(gen)
        assert "connected" in first_chunk
        assert "idle" in first_chunk
        await gen.aclose()
