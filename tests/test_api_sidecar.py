"""Phase 1 FastAPI sidecar — honesty, auth, lock, anti-reapprove."""

from __future__ import annotations

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


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


def _make_experiment(name: str = "api-exp-0") -> ChaosExperiment:
    return ChaosExperiment(
        name=name,
        target=TargetSpec(type=TargetType.SERVICE, name="test-svc"),
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

    def test_mutating_requires_operator_header(self, client: TestClient):
        for path in (
            "/v1/pending/0/approve",
            "/v1/pending/reject-all",
            "/v1/halt",
        ):
            resp = client.post(path)
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
        missing = tmp_path / "no-verdict.json"
        monkeypatch.setattr(
            "chaosgen.api.routes.verdict.load_verdict_report",
            lambda path=None: (_ for _ in ()).throw(FileNotFoundError("No expectation verdict")),
        )
        resp = client.get("/v1/verdict/last")
        assert resp.status_code == 404
