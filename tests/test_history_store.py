"""Tests for P5 — SQLite history store and pipeline persistence."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from chaosgen.advisor.pipeline import run_advisor_pipeline
from chaosgen.advisor.scenario_describer import ScenarioDescriber
from chaosgen.advisor.scenario_ranker import ScenarioRanker
from chaosgen.config.settings import ChaosGenSettings, HistorySettings
from chaosgen.ml.gatekeeper import IncidentGatekeeper
from chaosgen.schemas.faults import ChaosExperiment, FaultType, NetworkFaultSpec, TargetSpec, TargetType
from chaosgen.schemas.incidents import IncidentCandidate, IncidentVerdict
from chaosgen.schemas.scenarios import (
    AnomalyCluster,
    AnomalySeverity,
    AnomalySummary,
    ExperimentVerdict,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)
from chaosgen.storage.history import HistoryStore, SqliteLookbackStateStore


def _candidate(cluster_id=0, verdict=IncidentVerdict.REAL, service="payments"):
    return IncidentCandidate(
        cluster_id=cluster_id,
        frequency=2.5,
        severity=0.8,
        log_correlated=True,
        service_target=service,
        metadata={"error_pattern": "error: timeout"},
        verdict=verdict,
        rationale="test",
    )


def _summary(cluster_id=0, service="payments"):
    return AnomalySummary(
        service_name=service,
        top_features=[("latency", 2.0)],
        error_pattern="error: timeout",
        severity=0.8,
        time_window="2024-01-01",
        source_cluster_id=cluster_id,
    )


def _description(cluster_id=0):
    return UnknownScenarioDescription(
        title="Payment timeout spike",
        root_cause_hypothesis="Upstream dependency timeout on payment service",
        repro_steps=["Load test payments", "Watch p99 latency"],
        blast_radius_estimate="payments service only",
        suggested_fault_type=FaultType.NETWORK_LATENCY,
        confidence=0.8,
        source_incident_id=cluster_id,
    )


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "history.db"


@pytest.fixture
def store(db_path):
    return HistoryStore(db_path, async_writes=False)


class TestHistoryStoreCRUD:
    def test_begin_finish_run(self, store):
        run_id = store.begin_run(lookback_hours=4.0, skip_gatekeeper=False)
        store.finish_run(run_id, filtered_noise_count=3, report_path="/tmp/r.json")
        with store._lock:
            conn = store._open()
            try:
                row = conn.execute(
                    "SELECT filtered_noise_count, report_path FROM analysis_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
            finally:
                conn.close()
        assert row["filtered_noise_count"] == 3
        assert row["report_path"] == "/tmp/r.json"

    def test_record_incidents_bulk_skips_noise_verdict(self, store):
        run_id = store.begin_run(lookback_hours=1.0, skip_gatekeeper=False)
        noise = _candidate(verdict=IncidentVerdict.NOISE)
        real = _candidate(cluster_id=1, verdict=IncidentVerdict.REAL)
        mapping = store.record_incidents_bulk(
            run_id, [noise, real], {1: _summary(1)}
        )
        assert 1 in mapping
        assert 0 not in mapping
        assert store.count_incidents() == 1

    def test_persist_run_snapshot_roundtrip(self, store):
        run_id = store.begin_run(lookback_hours=2.0, skip_gatekeeper=False)
        candidates = [_candidate(0), _candidate(1, verdict=IncidentVerdict.TRANSIENT)]
        descriptions = [_description(0)]
        experiments = [
            ChaosExperiment(
                name="exp-0",
                description="test",
                target=TargetSpec(name="payments", type=TargetType.SERVICE),
                faults=[],
            )
        ]
        snap = store.persist_run_snapshot(
            run_id,
            candidates=candidates,
            summaries_by_cluster={0: _summary(0), 1: _summary(1)},
            descriptions=descriptions,
            experiments=experiments,
            sci_scores=[],
            filtered_noise_count=5,
            report_path="report.json",
            experiment_cluster_ids=[0],
        )
        assert snap.incident_row_ids[0] > 0
        assert snap.description_row_ids[0] > 0
        assert snap.experiment_row_ids["exp-0"] > 0
        rows = store.list_descriptions(run_id=run_id)
        assert len(rows) == 1
        assert rows[0]["title"] == "Payment timeout spike"

    def test_mark_promoted_updates_description(self, store):
        run_id = store.begin_run(lookback_hours=1.0, skip_gatekeeper=False)
        desc_id = store.record_description(run_id, _description(), incident_row_id=None)
        store.mark_promoted(desc_id, "catalog-entry", "operator")
        rows = store.list_descriptions(state=ScenarioKnowledgeState.KNOWN)
        assert rows[0]["promoted_catalog_name"] == "catalog-entry"

    def test_update_verdict_and_recent_fault_types(self, store):
        run_id = store.begin_run(lookback_hours=1.0, skip_gatekeeper=False)
        exp = ChaosExperiment(
            name="net-exp",
            description="d",
            target=TargetSpec(name="payments", type=TargetType.SERVICE),
            faults=[
                NetworkFaultSpec(
                    fault_type=FaultType.NETWORK_LATENCY,
                    latency="100ms",
                )
            ],
        )
        ids = store.record_experiments_bulk(run_id, [exp])
        store.update_verdict(
            ids["net-exp"], ExperimentVerdict.PASS, datetime.now(timezone.utc)
        )
        recent = store.recent_fault_types(days=7)
        assert FaultType.NETWORK_LATENCY in recent

    def test_get_chronic_patterns_groups_by_service_and_pattern(self, store):
        run_id = store.begin_run(lookback_hours=1.0, skip_gatekeeper=False)
        for _ in range(2):
            store.record_incidents_bulk(
                run_id,
                [_candidate(0, verdict=IncidentVerdict.CHRONIC)],
                {0: _summary(0)},
            )
            run_id = store.begin_run(lookback_hours=1.0, skip_gatekeeper=False)
        patterns = store.get_chronic_patterns(
            datetime.now(timezone.utc) - timedelta(days=1),
            min_occurrences=2,
        )
        assert len(patterns) == 1
        assert patterns[0].service_target == "payments"
        assert patterns[0].error_pattern == "error: timeout"
        assert patterns[0].occurrence_count >= 2

    def test_fresh_db_init_schema_version(self, db_path):
        store = HistoryStore(db_path, async_writes=False)
        with store._lock:
            conn = store._open()
            try:
                row = conn.execute(
                    "SELECT MAX(version) AS v FROM schema_version"
                ).fetchone()
            finally:
                conn.close()
        assert int(row["v"]) >= 1


class TestLookbackStateStore:
    def test_sqlite_lookback_roundtrip(self, store):
        lookback = SqliteLookbackStateStore(store)
        lookback.put("payments", {"samples": 10.0, "window_hours": 2.0})
        state = lookback.get("payments")
        assert state == {"samples": 10.0, "window_hours": 2.0}


class TestConcurrentWrites:
    def test_parallel_finish_run_no_crash(self, db_path):
        store = HistoryStore(db_path, async_writes=False)
        errors: list[Exception] = []

        def worker():
            try:
                run_id = store.begin_run(lookback_hours=1.0, skip_gatekeeper=False)
                store.record_incidents_bulk(
                    run_id, [_candidate(0)], {0: _summary(0)}
                )
                store.finish_run(run_id, filtered_noise_count=1)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert store.count_incidents() == 8


class TestPipelineHistoryWire:
    def test_pipeline_persists_when_history_enabled(self, db_path):
        settings = ChaosGenSettings(
            history=HistorySettings(enabled=True, db_path=str(db_path))
        )
        store = HistoryStore(db_path, async_writes=False)
        cluster = AnomalyCluster(
            cluster_id=0,
            severity=AnomalySeverity.HIGH,
            affected_services=["payments"],
            dominant_features=[("error_rate", 3.0)],
            sample_count=40,
        )
        summary = _summary(0)

        mock_describer = MagicMock(spec=ScenarioDescriber)
        mock_describer.describe_batch.return_value = [_description(0)]

        report = run_advisor_pipeline(
            [cluster],
            [summary],
            settings=settings,
            lookback_hours=4.0,
            gatekeeper=IncidentGatekeeper(settings=settings.gatekeeper),
            describer=mock_describer,
            llm_advisor=MagicMock(interpret_anomalies=MagicMock(return_value=[])),
            history_store=store,
            generate_chaos=False,
        )
        assert report.run_id is not None
        assert report.description_db_ids.get(0) is not None
        rows = store.list_descriptions(run_id=report.run_id)
        assert len(rows) == 1

    def test_pipeline_noise_count_without_incident_rows(self, db_path):
        settings = ChaosGenSettings(
            history=HistorySettings(enabled=True, db_path=str(db_path))
        )
        store = HistoryStore(db_path, async_writes=False)
        noise_cluster = AnomalyCluster(
            cluster_id=99,
            severity=AnomalySeverity.LOW,
            affected_services=["svc"],
            dominant_features=[("cpu", 1.0)],
            sample_count=1,
        )
        report = run_advisor_pipeline(
            [noise_cluster],
            [],
            settings=settings,
            lookback_hours=4.0,
            history_store=store,
            generate_chaos=False,
        )
        assert report.filtered_noise_count >= 1
        assert store.count_incidents() == 0


class TestScenarioRankerHistory:
    def test_ranker_reads_recent_fault_types_from_db(self, store):
        run_id = store.begin_run(lookback_hours=1.0, skip_gatekeeper=False)
        exp = ChaosExperiment(
            name="rank-exp",
            description="d",
            target=TargetSpec(name="payments", type=TargetType.SERVICE),
            faults=[
                NetworkFaultSpec(
                    fault_type=FaultType.PACKET_LOSS,
                    loss_percentage=10.0,
                )
            ],
        )
        ids = store.record_experiments_bulk(run_id, [exp])
        store.update_verdict(
            ids["rank-exp"], ExperimentVerdict.PASS, datetime.now(timezone.utc)
        )
        ranker = ScenarioRanker(history_store=store)
        recent = ranker._get_recent_fault_types()
        assert FaultType.PACKET_LOSS in recent


class TestAsyncWrites:
    def test_schedule_mark_promoted_flushes(self, db_path):
        store = HistoryStore(db_path, async_writes=True)
        run_id = store.begin_run(lookback_hours=1.0, skip_gatekeeper=False)
        desc_id = store.record_description(run_id, _description(), incident_row_id=None)
        store.schedule_mark_promoted(desc_id, "cat", "op")
        store.flush()
        rows = store.list_descriptions(state=ScenarioKnowledgeState.KNOWN)
        assert rows[0]["promoted_catalog_name"] == "cat"


class TestCorruptDbRecovery:
    def test_corrupt_db_reinitializes(self, db_path):
        db_path.write_bytes(b"not-a-database")
        store = HistoryStore(db_path, async_writes=False)
        run_id = store.begin_run(lookback_hours=1.0, skip_gatekeeper=False)
        assert run_id == 1
