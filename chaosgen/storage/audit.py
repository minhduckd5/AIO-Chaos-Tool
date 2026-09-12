"""
Operator audit trail (P1) — append-only JSONL source of truth.

Write strategy is deliberately different from :class:`PromotedStore`:
``append_jsonl_line`` shares ``ExclusiveFileLock`` but never rewrites the file
and never calls ``replace_with_retry`` on the live JSONL. See
docs/audit-log-schema-proposal.md.

Emit order (locked): resolve actor -> JSONL append (required) -> SQLite mirror
(best-effort, warning only).
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from chaosgen.config.paths import CONFIG_DIR, ensure_config_dir
from chaosgen.storage.atomic_io import append_jsonl_line, iter_jsonl

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
AUDIT_FILENAME = "audit_events.jsonl"
AUDIT_PATH_ENV = "CHAOSGEN_AUDIT_LOG"

EventType = Literal[
    "queued",
    "approved",
    "rejected",
    "inject_started",
    "inject_finished",
    "rollback",
    "hatch_used",
    "orphan_sweep",
]
PathUsed = Literal[
    "ai_hitl",
    "api_hitl",
    "api_config",
    "operator_direct",
    "skip_gatekeeper",
    "cli_approve_all_force",
    "module_direct",
]
Outcome = Literal["success", "failure", "aborted", "dry_run", "blocked"]


class AuditActorRequired(RuntimeError):
    """Raised when no operator identity can be resolved (A8)."""


class BlastRadiusRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespaces: list[str] = Field(default_factory=list)
    blocked_namespaces: list[str] = Field(default_factory=list)
    validation_ok: Optional[bool] = None


class CriteriaRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: Optional[str] = None


class TargetClusterContext(BaseModel):
    """Evidence of *where* an inject was aimed (A5)."""

    model_config = ConfigDict(extra="forbid")

    kube_context: Optional[str] = None
    kube_namespace: Optional[str] = None
    docker_host: Optional[str] = None
    environment_hint: Optional[str] = None

    def is_resolvable(self) -> bool:
        return any(
            (value or "").strip()
            for value in (
                self.kube_context,
                self.kube_namespace,
                self.docker_host,
                self.environment_hint,
            )
        )


class AuditEvent(BaseModel):
    """Audit event schema v1 (see docs/audit-log-schema-proposal.md)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    actor: str = Field(min_length=1)
    event_type: EventType
    path_used: PathUsed
    run_id: Optional[str] = None
    experiment_name: Optional[str] = None
    blast_radius_ref: Optional[BlastRadiusRef] = None
    criteria_ref: Optional[CriteriaRef] = None
    target_cluster_context: Optional[TargetClusterContext] = None
    outcome: Optional[Outcome] = None
    notes: Optional[str] = None


def criteria_ref_for_path(path: Path | str) -> Optional[CriteriaRef]:
    """Build a ``criteria_ref`` matching the promote criteria file (A6)."""
    target = Path(path)
    if not target.is_file():
        return None
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return CriteriaRef(path=str(target), sha256=digest)


def default_audit_path() -> Path:
    override = os.environ.get(AUDIT_PATH_ENV)
    if override:
        return Path(override)
    return CONFIG_DIR / AUDIT_FILENAME


def resolve_actor(
    *,
    settings: Any | None = None,
    prompt: Optional[Callable[[], Optional[str]]] = None,
    persist: bool = True,
    settings_path: str | None = None,
) -> str:
    """
    Resolve the audit actor (A8).

    Order: configured ``operator_name`` -> one-time ``prompt`` (persisted) ->
    :class:`AuditActorRequired`. Never falls back to a synthetic identity: an
    unattributable trail cannot answer "who approved what".
    """
    from chaosgen.config.settings import load_settings, save_settings

    resolved_settings = settings
    if resolved_settings is None:
        resolved_settings = load_settings(settings_path)

    configured = (getattr(resolved_settings, "operator_name", None) or "").strip()
    if configured:
        return configured

    entered = (prompt() or "").strip() if prompt is not None else ""
    if not entered:
        raise AuditActorRequired(
            "No operator identity configured. Set it with "
            "`chaosgen config set-operator <name>` — audit events are never "
            "attributed to a synthetic default."
        )

    if persist:
        try:
            resolved_settings.operator_name = entered
            save_settings(resolved_settings, settings_path)
        except Exception as exc:  # pragma: no cover — best-effort persistence
            logger.warning("Could not persist operator_name: %s", exc)
    return entered


def emit_best_effort(
    *,
    event_type: EventType,
    path_used: PathUsed,
    settings: Any | None = None,
    history_store: Any | None = None,
    **fields: Any,
) -> Optional["AuditEvent"]:
    """
    Emit one event without ever raising into the caller's flow.

    For non-interactive call sites (pipeline hooks): actor comes from configured
    ``operator_name`` only. An unresolvable actor logs a warning and drops the
    event rather than inventing an identity.
    """
    try:
        actor = resolve_actor(settings=settings, persist=False)
    except AuditActorRequired:
        logger.warning(
            "Audit event %s not recorded: no operator_name configured", event_type
        )
        return None
    try:
        store = AuditStore(history_store=history_store)
        return store.emit(
            event_type=event_type, path_used=path_used, actor=actor, **fields
        )
    except Exception as exc:
        logger.warning("Audit emit failed for %s: %s", event_type, exc)
        return None


class AuditStore:
    """Append-only audit event store (JSONL SOT + optional SQLite mirror)."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        history_store: Any | None = None,
        actor: str | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else default_audit_path()
        self._history_store = history_store
        self._actor = (actor or "").strip() or None
        self._mirror_disabled = False
        if path is None and not os.environ.get(AUDIT_PATH_ENV):
            try:
                ensure_config_dir()
            except OSError as exc:  # pragma: no cover — surfaced on emit anyway
                logger.warning("Could not prepare audit directory: %s", exc)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def actor(self) -> Optional[str]:
        return self._actor

    def set_actor(self, actor: str | None) -> None:
        self._actor = (actor or "").strip() or None

    def emit(
        self,
        *,
        event_type: EventType,
        path_used: PathUsed,
        actor: str | None = None,
        run_id: str | None = None,
        experiment_name: str | None = None,
        blast_radius_ref: BlastRadiusRef | None = None,
        criteria_ref: CriteriaRef | None = None,
        target_cluster_context: TargetClusterContext | None = None,
        outcome: Outcome | None = None,
        notes: str | None = None,
    ) -> AuditEvent:
        resolved_actor = (actor or self._actor or "").strip()
        if not resolved_actor:
            raise AuditActorRequired(
                "audit emit requires an actor; resolve operator_name first (A8)"
            )

        event = AuditEvent(
            actor=resolved_actor,
            event_type=event_type,
            path_used=path_used,
            run_id=run_id,
            experiment_name=experiment_name,
            blast_radius_ref=blast_radius_ref,
            criteria_ref=criteria_ref,
            target_cluster_context=target_cluster_context,
            outcome=outcome,
            notes=notes,
        )
        record = event.model_dump(mode="json")

        # JSONL is the source of truth: a failure here fails the emit.
        append_jsonl_line(self._path, record)
        self._mirror(record)
        return event

    def iter_events(self) -> Iterator[AuditEvent]:
        """Yield valid events, skipping unparsable rows (A9)."""
        for raw in iter_jsonl(self._path):
            try:
                yield AuditEvent.model_validate(raw)
            except Exception as exc:
                logger.warning(
                    "Skipping audit row failing schema v%d validation: %s",
                    SCHEMA_VERSION,
                    exc,
                )

    def read_events(self) -> list[AuditEvent]:
        return list(self.iter_events())

    def _mirror(self, record: dict[str, Any]) -> None:
        """Best-effort SQLite mirror: warn on failure, never rollback JSONL."""
        if self._history_store is None or self._mirror_disabled:
            return
        recorder = getattr(self._history_store, "record_audit_event", None)
        if recorder is None:
            self._mirror_disabled = True
            logger.warning("History store cannot mirror audit events; skipping mirror")
            return
        try:
            recorder(record)
        except Exception as exc:
            logger.warning(
                "Audit SQLite mirror failed for event %s (JSONL kept as SOT): %s",
                record.get("event_id"),
                exc,
            )
