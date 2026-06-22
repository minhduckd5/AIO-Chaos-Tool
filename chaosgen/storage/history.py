"""
SQLite history / analytics store (P5 — SQLite History Loop).

Append-only cross-run history for ScenarioRanker coverage gap, CHRONIC pattern
detection, and audit. JSON files (P3/P4) remain runtime source of truth.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from chaosgen.config.paths import CONFIG_DIR, ensure_config_dir
from chaosgen.schemas.faults import ChaosExperiment, FaultType
from chaosgen.schemas.incidents import IncidentCandidate, IncidentVerdict
from chaosgen.schemas.scenarios import (
    ExperimentVerdict,
    ScenarioComplexityIndex,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_HISTORY_DB = CONFIG_DIR / "history.db"

_CREATE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        lookback_hours REAL NOT NULL,
        skip_gatekeeper INTEGER NOT NULL DEFAULT 0,
        filtered_noise_count INTEGER NOT NULL DEFAULT 0,
        report_path TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        cluster_id INTEGER NOT NULL,
        service_target TEXT,
        error_pattern TEXT,
        verdict TEXT NOT NULL,
        frequency REAL NOT NULL,
        severity REAL NOT NULL,
        log_correlated INTEGER NOT NULL DEFAULT 0,
        rationale TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES analysis_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS descriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        incident_row_id INTEGER,
        source_incident_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        json_body TEXT NOT NULL,
        knowledge_state TEXT NOT NULL,
        describe_fallback INTEGER NOT NULL DEFAULT 0,
        promoted_catalog_name TEXT,
        approved_by TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES analysis_runs(id),
        FOREIGN KEY (incident_row_id) REFERENCES incidents(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS experiments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        description_row_id INTEGER,
        name TEXT NOT NULL,
        fault_types_json TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'llm',
        sci_score REAL,
        verdict TEXT,
        ran_at TEXT,
        approved_by TEXT,
        FOREIGN KEY (run_id) REFERENCES analysis_runs(id),
        FOREIGN KEY (description_row_id) REFERENCES descriptions(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lookback_state (
        key TEXT PRIMARY KEY,
        samples REAL NOT NULL,
        window_hours REAL NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_incidents_run ON incidents(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_service_pattern ON incidents(service_target, error_pattern)",
    "CREATE INDEX IF NOT EXISTS idx_experiments_ran_at ON experiments(ran_at)",
    "CREATE INDEX IF NOT EXISTS idx_descriptions_run ON descriptions(run_id)",
]


@dataclass
class ChronicPattern:
    service_target: str
    error_pattern: str
    occurrence_count: int
    last_seen: datetime


@dataclass
class PersistedRunSnapshot:
    """IDs produced by a pipeline persist batch."""

    incident_row_ids: Dict[int, int]
    description_row_ids: Dict[int, int]
    experiment_row_ids: Dict[str, int]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class HistoryStore:
    """SQLite-backed analytics history (not runtime SOT)."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        async_writes: bool = True,
    ) -> None:
        self._db_path = Path(db_path) if db_path is not None else DEFAULT_HISTORY_DB
        self._lock = threading.RLock()
        self._async_writes = async_writes
        self._executor: Optional[ThreadPoolExecutor] = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-store")
            if async_writes
            else None
        )
        self._pending: List[Future] = []
        ensure_config_dir()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def lookback_store(self) -> "SqliteLookbackStateStore":
        return SqliteLookbackStateStore(self)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=5.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")

    def _open(self) -> sqlite3.Connection:
        conn = self._connect()
        try:
            self._configure_connection(conn)
        except sqlite3.DatabaseError:
            conn.close()
            raise
        return conn

    def _init_db(self) -> None:
        try:
            self._apply_schema()
        except sqlite3.DatabaseError as exc:
            self._recover_corrupt_db(exc)
            self._apply_schema()

    def _apply_schema(self) -> None:
        with self._lock:
            conn = self._open()
            try:
                for stmt in _CREATE_STATEMENTS:
                    conn.execute(stmt)
                row = conn.execute(
                    "SELECT MAX(version) AS v FROM schema_version"
                ).fetchone()
                current = int(row["v"]) if row and row["v"] is not None else 0
                if current < SCHEMA_VERSION:
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (SCHEMA_VERSION, _iso(_utc_now())),
                    )
                conn.commit()
            finally:
                conn.close()

    def _recover_corrupt_db(self, exc: Exception) -> None:
        logger.error(
            "History DB corrupt or unreadable (%s); quarantining %s",
            exc,
            self._db_path,
        )
        if not self._db_path.exists():
            return
        quarantine = self._db_path.with_suffix(".db.corrupt")
        try:
            if quarantine.exists():
                quarantine.unlink()
            shutil.move(str(self._db_path), str(quarantine))
        except OSError as move_exc:
            logger.warning("Could not quarantine history DB: %s", move_exc)
            try:
                self._db_path.unlink()
            except OSError as unlink_exc:
                logger.warning("Could not remove corrupt history DB: %s", unlink_exc)

    def _submit(self, fn, *args, **kwargs) -> None:
        if self._executor is None:
            fn(*args, **kwargs)
            return
        future = self._executor.submit(fn, *args, **kwargs)
        self._pending.append(future)

    def flush(self, timeout: float = 30.0) -> None:
        """Wait for async writes (tests / shutdown)."""
        if not self._pending:
            return
        for future in self._pending:
            future.result(timeout=timeout)
        self._pending.clear()

    def begin_run(self, *, lookback_hours: float, skip_gatekeeper: bool) -> int:
        with self._lock:
            conn = self._open()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO analysis_runs
                        (created_at, lookback_hours, skip_gatekeeper, filtered_noise_count)
                    VALUES (?, ?, ?, 0)
                    """,
                    (_iso(_utc_now()), lookback_hours, int(skip_gatekeeper)),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    def finish_run(
        self,
        run_id: int,
        *,
        filtered_noise_count: int,
        report_path: str | None = None,
    ) -> None:
        with self._lock:
            conn = self._open()
            try:
                conn.execute(
                    """
                    UPDATE analysis_runs
                    SET filtered_noise_count = ?, report_path = ?
                    WHERE id = ?
                    """,
                    (filtered_noise_count, report_path, run_id),
                )
                conn.commit()
            finally:
                conn.close()

    def persist_run_snapshot(
        self,
        run_id: int,
        *,
        candidates: List[IncidentCandidate],
        summaries_by_cluster: Dict[int, Any],
        descriptions: List[UnknownScenarioDescription],
        experiments: List[ChaosExperiment],
        sci_scores: List[ScenarioComplexityIndex],
        filtered_noise_count: int,
        report_path: str | None = None,
        experiment_cluster_ids: List[int | None] | None = None,
    ) -> PersistedRunSnapshot:
        """Bulk persist one pipeline run (single transaction, post-LLM)."""
        incident_ids = self.record_incidents_bulk(
            run_id, candidates, summaries_by_cluster
        )
        description_ids: Dict[int, int] = {}
        for desc in descriptions:
            incident_row_id = incident_ids.get(desc.source_incident_id)
            row_id = self.record_description(
                run_id, desc, incident_row_id=incident_row_id
            )
            description_ids[desc.source_incident_id] = row_id

        sci_by_name = {
            experiments[i].name: sci_scores[i].weighted_score
            for i in range(min(len(experiments), len(sci_scores)))
        }
        exp_desc_rows: List[int | None] = []
        cluster_ids = experiment_cluster_ids or [None] * len(experiments)
        for i in range(len(experiments)):
            cluster_id = cluster_ids[i] if i < len(cluster_ids) else None
            exp_desc_rows.append(
                description_ids.get(cluster_id) if cluster_id is not None else None
            )
        experiment_ids = self.record_experiments_bulk(
            run_id,
            experiments,
            experiment_description_row_ids=exp_desc_rows,
            sci_by_name=sci_by_name,
        )
        self.finish_run(
            run_id,
            filtered_noise_count=filtered_noise_count,
            report_path=report_path,
        )
        return PersistedRunSnapshot(
            incident_row_ids=incident_ids,
            description_row_ids=description_ids,
            experiment_row_ids=experiment_ids,
        )

    def schedule_persist_run(
        self,
        run_id: int,
        *,
        candidates: List[IncidentCandidate],
        summaries_by_cluster: Dict[int, Any],
        descriptions: List[UnknownScenarioDescription],
        experiments: List[ChaosExperiment],
        sci_scores: List[ScenarioComplexityIndex],
        filtered_noise_count: int,
        report_path: str | None = None,
        experiment_cluster_ids: List[int | None] | None = None,
        on_complete=None,
    ) -> None:
        """Fire-and-forget persist (promoter / background analytics)."""

        def _work() -> PersistedRunSnapshot:
            snap = self.persist_run_snapshot(
                run_id,
                candidates=candidates,
                summaries_by_cluster=summaries_by_cluster,
                descriptions=descriptions,
                experiments=experiments,
                sci_scores=sci_scores,
                filtered_noise_count=filtered_noise_count,
                report_path=report_path,
                experiment_cluster_ids=experiment_cluster_ids,
            )
            if on_complete:
                on_complete(snap)
            return snap

        self._submit(_work)

    def record_incidents_bulk(
        self,
        run_id: int,
        candidates: List[IncidentCandidate],
        summaries_by_cluster: Dict[int, Any],
    ) -> Dict[int, int]:
        """Persist TRANSIENT+ candidates; NOISE must not appear in candidates."""
        mapping: Dict[int, int] = {}
        if not candidates:
            return mapping

        now = _iso(_utc_now())
        with self._lock:
            conn = self._open()
            try:
                for candidate in candidates:
                    if candidate.verdict == IncidentVerdict.NOISE:
                        continue
                    summary = summaries_by_cluster.get(candidate.cluster_id)
                    error_pattern = candidate.metadata.get("error_pattern")
                    if not error_pattern and summary is not None:
                        error_pattern = getattr(summary, "error_pattern", None)
                    cur = conn.execute(
                        """
                        INSERT INTO incidents (
                            run_id, cluster_id, service_target, error_pattern,
                            verdict, frequency, severity, log_correlated,
                            rationale, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            candidate.cluster_id,
                            candidate.service_target,
                            error_pattern,
                            candidate.verdict.value,
                            candidate.frequency,
                            candidate.severity,
                            int(candidate.log_correlated),
                            candidate.rationale,
                            now,
                        ),
                    )
                    mapping[candidate.cluster_id] = int(cur.lastrowid)
                conn.commit()
            finally:
                conn.close()
        return mapping

    def record_description(
        self,
        run_id: int,
        desc: UnknownScenarioDescription,
        *,
        incident_row_id: int | None = None,
    ) -> int:
        with self._lock:
            conn = self._open()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO descriptions (
                        run_id, incident_row_id, source_incident_id, title,
                        json_body, knowledge_state, describe_fallback, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        incident_row_id,
                        desc.source_incident_id,
                        desc.title,
                        json.dumps(desc.model_dump(mode="json")),
                        desc.knowledge_state.value,
                        int(bool(desc.metadata.get("describe_fallback"))),
                        _iso(_utc_now()),
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    def record_experiments_bulk(
        self,
        run_id: int,
        experiments: List[ChaosExperiment],
        *,
        experiment_description_row_ids: List[int | None] | None = None,
        sci_by_name: Dict[str, float] | None = None,
        source: str = "llm",
    ) -> Dict[str, int]:
        mapping: Dict[str, int] = {}
        if not experiments:
            return mapping

        sci_by_name = sci_by_name or {}
        desc_rows = experiment_description_row_ids or [None] * len(experiments)
        with self._lock:
            conn = self._open()
            try:
                for i, exp in enumerate(experiments):
                    fault_types = [f.fault_type.value for f in exp.faults]
                    desc_row_id = desc_rows[i] if i < len(desc_rows) else None
                    cur = conn.execute(
                        """
                        INSERT INTO experiments (
                            run_id, description_row_id, name, fault_types_json,
                            source, sci_score
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            desc_row_id,
                            exp.name,
                            json.dumps(fault_types),
                            source,
                            sci_by_name.get(exp.name),
                        ),
                    )
                    mapping[exp.name] = int(cur.lastrowid)
                conn.commit()
            finally:
                conn.close()
        return mapping

    def mark_promoted(
        self,
        description_row_id: int,
        catalog_name: str,
        approved_by: str,
    ) -> None:
        with self._lock:
            conn = self._open()
            try:
                conn.execute(
                    """
                    UPDATE descriptions
                    SET knowledge_state = ?, promoted_catalog_name = ?, approved_by = ?
                    WHERE id = ?
                    """,
                    (
                        ScenarioKnowledgeState.KNOWN.value,
                        catalog_name,
                        approved_by.strip(),
                        description_row_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def schedule_mark_promoted(
        self,
        description_row_id: int,
        catalog_name: str,
        approved_by: str,
    ) -> None:
        self._submit(
            self.mark_promoted, description_row_id, catalog_name, approved_by
        )

    def update_verdict(
        self,
        experiment_row_id: int,
        verdict: ExperimentVerdict,
        ran_at: datetime | None = None,
    ) -> None:
        ran = ran_at or _utc_now()
        with self._lock:
            conn = self._open()
            try:
                conn.execute(
                    """
                    UPDATE experiments
                    SET verdict = ?, ran_at = ?
                    WHERE id = ?
                    """,
                    (verdict.value, _iso(ran), experiment_row_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_chronic_patterns(
        self,
        since: datetime,
        *,
        min_occurrences: int = 2,
    ) -> List[ChronicPattern]:
        since_iso = _iso(since)
        with self._lock:
            conn = self._open()
            try:
                rows = conn.execute(
                    """
                    SELECT
                        COALESCE(service_target, 'unknown') AS service_target,
                        COALESCE(error_pattern, '') AS error_pattern,
                        COUNT(*) AS occurrence_count,
                        MAX(created_at) AS last_seen
                    FROM incidents
                    WHERE created_at >= ?
                      AND verdict IN ('real', 'chronic')
                    GROUP BY service_target, error_pattern
                    HAVING occurrence_count >= ?
                    ORDER BY occurrence_count DESC, last_seen DESC
                    """,
                    (since_iso, min_occurrences),
                ).fetchall()
            finally:
                conn.close()

        return [
            ChronicPattern(
                service_target=row["service_target"],
                error_pattern=row["error_pattern"],
                occurrence_count=int(row["occurrence_count"]),
                last_seen=_parse_iso(row["last_seen"]),
            )
            for row in rows
        ]

    def recent_fault_types(self, days: int = 7) -> Set[FaultType]:
        cutoff = _iso(_utc_now() - timedelta(days=days))
        found: Set[FaultType] = set()
        with self._lock:
            conn = self._open()
            try:
                rows = conn.execute(
                    """
                    SELECT fault_types_json FROM experiments
                    WHERE ran_at IS NOT NULL AND ran_at >= ?
                    """,
                    (cutoff,),
                ).fetchall()
            finally:
                conn.close()

        for row in rows:
            try:
                for value in json.loads(row["fault_types_json"]):
                    found.add(FaultType(value))
            except (json.JSONDecodeError, ValueError):
                continue
        return found

    def list_descriptions(
        self,
        *,
        run_id: int | None = None,
        state: ScenarioKnowledgeState | None = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if state is not None:
            clauses.append("knowledge_state = ?")
            params.append(state.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._lock:
            conn = self._open()
            try:
                rows = conn.execute(
                    f"""
                    SELECT id, run_id, source_incident_id, title, knowledge_state,
                           describe_fallback, promoted_catalog_name, json_body, created_at
                    FROM descriptions
                    {where}
                    ORDER BY created_at DESC
                    """,
                    params,
                ).fetchall()
            finally:
                conn.close()

        results: List[Dict[str, Any]] = []
        for row in rows:
            body = json.loads(row["json_body"])
            results.append(
                {
                    "id": row["id"],
                    "run_id": row["run_id"],
                    "source_incident_id": row["source_incident_id"],
                    "title": row["title"],
                    "knowledge_state": row["knowledge_state"],
                    "describe_fallback": bool(row["describe_fallback"]),
                    "promoted_catalog_name": row["promoted_catalog_name"],
                    "description": body,
                    "created_at": row["created_at"],
                }
            )
        return results

    def count_incidents(self, *, verdict: IncidentVerdict | None = None) -> int:
        with self._lock:
            conn = self._open()
            try:
                if verdict is None:
                    row = conn.execute("SELECT COUNT(*) AS c FROM incidents").fetchone()
                else:
                    row = conn.execute(
                        "SELECT COUNT(*) AS c FROM incidents WHERE verdict = ?",
                        (verdict.value,),
                    ).fetchone()
            finally:
                conn.close()
        return int(row["c"])


class SqliteLookbackStateStore:
    """SQLite-backed ``LookbackStateStore`` for cross-batch gatekeeper frequency."""

    def __init__(self, store: HistoryStore) -> None:
        self._store = store

    def get(self, key: str) -> Optional[Dict[str, float]]:
        with self._store._lock:
            conn = self._store._open()
            try:
                row = conn.execute(
                    "SELECT samples, window_hours FROM lookback_state WHERE key = ?",
                    (key,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return {"samples": float(row["samples"]), "window_hours": float(row["window_hours"])}

    def put(self, key: str, state: Dict[str, float]) -> None:
        with self._store._lock:
            conn = self._store._open()
            try:
                conn.execute(
                    """
                    INSERT INTO lookback_state (key, samples, window_hours, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        samples = excluded.samples,
                        window_hours = excluded.window_hours,
                        updated_at = excluded.updated_at
                    """,
                    (
                        key,
                        float(state.get("samples", 0.0)),
                        float(state.get("window_hours", 0.0)),
                        _iso(_utc_now()),
                    ),
                )
                conn.commit()
            finally:
                conn.close()


def get_default_history_store(
    settings: Any | None = None,
    *,
    async_writes: bool = True,
) -> HistoryStore | None:
    from chaosgen.config.settings import ChaosGenSettings, load_settings

    cfg: ChaosGenSettings = settings or load_settings()
    if not cfg.history.enabled:
        return None
    path = Path(cfg.history.db_path) if cfg.history.db_path else DEFAULT_HISTORY_DB
    return HistoryStore(path, async_writes=async_writes and cfg.history.async_writes)
