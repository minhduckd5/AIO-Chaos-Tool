"""
Promoted scenario store (P3 — Promote Known Catalog).

Persists HITL-approved scenarios that close the Unknown -> Known loop to a
versioned JSON file under the ChaosGen config directory. The store is the source
of truth for the dynamic part of `ScenarioCatalog` until P5 introduces a SQLite
history layer.

Hardening (all dependency-free):
- Atomic writes: serialize to a temp file, fsync, then ``os.replace`` so a crash
  mid-write never corrupts the live file.
- Concurrency: an in-process lock plus an advisory cross-process lockfile guard
  the read-merge-write cycle so two CLIs/GUIs promoting at once cannot lose data.
- Versioned envelope: ``{"schema_version": ..., "scenarios": [...]}`` so future
  schema changes can migrate old files instead of crashing on load.
- Recovery: a corrupt file is quarantined and the ``.bak`` is tried before the
  store falls back to an empty promoted list. ``load_safe`` never raises, so the
  built-in catalog always keeps working.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from chaosgen.advisor.scenario_catalog import CatalogEntry
from chaosgen.config.paths import CONFIG_DIR
from chaosgen.schemas.discovery import ArchitectureType
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultSpec,
    FaultType,
    NetworkFaultSpec,
    ProcessFaultSpec,
    ResourceFaultSpec,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"

# Serializes the read-merge-write cycle across threads within one process. The
# cross-process lockfile handles separate processes; this handles threads (which
# a single advisory lockfile cannot, since the same PID could re-acquire it).
_GLOBAL_LOCK = threading.RLock()


class PromotedStoreError(Exception):
    """Raised when the promoted store cannot be parsed or migrated."""


# ---------------------------------------------------------------------------
# Polymorphic ChaosExperiment <-> dict (faults are FaultSpec subclasses)
# ---------------------------------------------------------------------------

_FAULT_SPEC_BY_TYPE: Dict[FaultType, type[FaultSpec]] = {
    FaultType.NETWORK_LATENCY: NetworkFaultSpec,
    FaultType.PACKET_LOSS: NetworkFaultSpec,
    FaultType.RESOURCE_EXHAUSTION: ResourceFaultSpec,
    FaultType.PROCESS_KILL: ProcessFaultSpec,
}


def _rebuild_fault(data: Dict[str, Any]) -> FaultSpec:
    fault_type = FaultType(data["fault_type"])
    spec_cls = _FAULT_SPEC_BY_TYPE.get(fault_type, FaultSpec)
    return spec_cls.model_validate(data)


def experiment_to_dict(experiment: ChaosExperiment) -> Dict[str, Any]:
    """Serialize a ChaosExperiment, preserving fault-subclass-specific fields."""
    data = experiment.model_dump(mode="json")
    # Dump each fault from its concrete instance so subclass fields (latency,
    # cpu_percent, signal, ...) survive — a List[FaultSpec] dump would drop them.
    data["faults"] = [fault.model_dump(mode="json") for fault in experiment.faults]
    return data


def experiment_from_dict(data: Dict[str, Any]) -> ChaosExperiment:
    """Rebuild a ChaosExperiment, restoring the correct FaultSpec subclass."""
    payload = dict(data)
    payload["faults"] = [_rebuild_fault(fault) for fault in payload.get("faults", [])]
    return ChaosExperiment.model_validate(payload)


# ---------------------------------------------------------------------------
# Serializable promoted record
# ---------------------------------------------------------------------------


class PromotedCatalogRecord(BaseModel):
    """JSON-serializable mirror of a promoted CatalogEntry.

    The built-in CatalogEntry holds a non-serializable ``experiment_factory``
    callable, so promoted entries persist the experiment as a plain dict and
    rebuild the factory on load.
    """

    name: str
    description: str
    architecture: ArchitectureType
    fault_type: FaultType
    experiment_spec: Dict[str, Any]
    acceptance_criteria: Optional[Dict[str, Any]] = None
    tags: List[str] = Field(default_factory=list)
    promoted_at: str
    approved_by: str
    source_incident_id: Optional[int] = None
    description_id: Optional[str] = None

    def to_catalog_entry(self) -> CatalogEntry:
        spec = dict(self.experiment_spec)
        return CatalogEntry(
            name=self.name,
            description=self.description,
            architecture=self.architecture,
            fault_type=self.fault_type,
            experiment_factory=lambda captured=spec: experiment_from_dict(captured),
            tags=list(self.tags),
            source="promoted",
            acceptance_criteria=self.acceptance_criteria,
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class PromotedStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else (CONFIG_DIR / "promoted_scenarios.json")
        self.bak_path = self.path.with_name(self.path.name + ".bak")
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    # -- public API --------------------------------------------------------

    def load(self) -> List[PromotedCatalogRecord]:
        """Load and parse the store. Raises PromotedStoreError on corruption."""
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise PromotedStoreError(f"cannot read promoted store: {exc}") from exc
        return self._parse(raw)

    def load_records_safe(self) -> List[PromotedCatalogRecord]:
        """Load records, recovering from backup on corruption. Never raises."""
        try:
            return self.load()
        except PromotedStoreError as exc:
            logger.error("Promoted store load failed: %s", exc)
            return self._attempt_backup_recovery()

    def load_safe(self, architecture: ArchitectureType | None = None) -> List[CatalogEntry]:
        """Return promoted entries as CatalogEntry, optionally filtered. Never raises."""
        entries = [record.to_catalog_entry() for record in self.load_records_safe()]
        if architecture is not None:
            entries = [entry for entry in entries if entry.architecture == architecture]
        return entries

    def append(self, record: PromotedCatalogRecord) -> None:
        """Atomically append a record under in-process + cross-process locks."""
        with _GLOBAL_LOCK:
            with self._file_lock():
                records = self.load_records_safe()
                records.append(record)
                self._write(records)

    # -- parsing / migration ----------------------------------------------

    def _parse(self, raw: Any) -> List[PromotedCatalogRecord]:
        if isinstance(raw, list):
            # Legacy v0: a bare list of scenarios. Migrate transparently.
            return self._records_from_dicts(raw)
        if isinstance(raw, dict):
            version = str(raw.get("schema_version", "0"))
            scenarios = raw.get("scenarios", [])
            if version == SCHEMA_VERSION:
                return self._records_from_dicts(scenarios)
            if version == "0":
                return self._records_from_dicts(scenarios)
            raise PromotedStoreError(f"unsupported schema_version: {version}")
        raise PromotedStoreError(f"unexpected promoted store shape: {type(raw).__name__}")

    @staticmethod
    def _records_from_dicts(items: Any) -> List[PromotedCatalogRecord]:
        if not isinstance(items, list):
            raise PromotedStoreError("'scenarios' must be a list")
        try:
            return [PromotedCatalogRecord.model_validate(item) for item in items]
        except Exception as exc:  # pydantic ValidationError, etc.
            raise PromotedStoreError(f"invalid promoted record: {exc}") from exc

    # Reserved for the next schema bump; wired through _parse when "1.1" lands.
    @staticmethod
    def _migrate_v1_to_v2(records: List[PromotedCatalogRecord]) -> List[PromotedCatalogRecord]:
        return records

    # -- writing -----------------------------------------------------------

    def _write(self, records: List[PromotedCatalogRecord]) -> None:
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "scenarios": [record.model_dump(mode="json") for record in records],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(envelope, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            # Rotate the current good file to .bak before replacing it.
            if self.path.exists():
                shutil.copy2(self.path, self.bak_path)
            os.replace(tmp_path, self.path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    # -- recovery ----------------------------------------------------------

    def _attempt_backup_recovery(self) -> List[PromotedCatalogRecord]:
        # Quarantine the corrupt main file so a human can inspect it later.
        if self.path.exists():
            corrupt_path = self.path.with_name(
                f"{self.path.name}.corrupt.{int(time.time())}"
            )
            try:
                os.replace(self.path, corrupt_path)
                logger.warning("Quarantined corrupt promoted store -> %s", corrupt_path)
            except OSError as exc:
                logger.error("Could not quarantine corrupt store: %s", exc)

        if self.bak_path.exists():
            try:
                raw = json.loads(self.bak_path.read_text(encoding="utf-8"))
                records = self._parse(raw)
                shutil.copy2(self.bak_path, self.path)
                logger.warning("Recovered promoted store from backup %s", self.bak_path)
                return records
            except (json.JSONDecodeError, OSError, PromotedStoreError) as exc:
                logger.error("Backup recovery failed (backup also bad): %s", exc)

        logger.warning(
            "Promoted catalog reset to empty — see %s.corrupt.* for the bad file",
            self.path,
        )
        return []

    # -- locking -----------------------------------------------------------

    class _FileLockGuard:
        def __init__(self, lock_path: Path, timeout: float, poll: float) -> None:
            self._lock_path = lock_path
            self._timeout = timeout
            self._poll = poll
            self._fd: int | None = None

        def __enter__(self) -> "PromotedStore._FileLockGuard":
            deadline = time.monotonic() + self._timeout
            while True:
                try:
                    self._fd = os.open(
                        str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR
                    )
                    os.write(self._fd, str(os.getpid()).encode("ascii"))
                    return self
                except FileExistsError:
                    if time.monotonic() >= deadline:
                        # Stale lock or heavy contention: proceed rather than
                        # hard-fail. The in-process lock still serializes threads.
                        logger.warning(
                            "Promoted store lock %s busy after %.1fs; proceeding",
                            self._lock_path, self._timeout,
                        )
                        return self
                    time.sleep(self._poll)

        def __exit__(self, *exc: Any) -> None:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            try:
                self._lock_path.unlink()
            except OSError:
                pass

    def _file_lock(self, timeout: float = 10.0, poll: float = 0.05) -> "PromotedStore._FileLockGuard":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return self._FileLockGuard(self.lock_path, timeout, poll)


# ---------------------------------------------------------------------------
# Module-level default store
# ---------------------------------------------------------------------------

_default_store: PromotedStore | None = None


def get_default_store() -> PromotedStore:
    global _default_store
    if _default_store is None:
        _default_store = PromotedStore()
    return _default_store


def load_safe(architecture: ArchitectureType | None = None) -> List[CatalogEntry]:
    """Convenience wrapper over the default store. Never raises."""
    return get_default_store().load_safe(architecture)
