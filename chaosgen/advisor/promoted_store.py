"""
Promoted scenario store (P3 — Promote Known Catalog).

Persists HITL-approved scenarios that close the Unknown -> Known loop to a
versioned JSON file under the ChaosGen config directory. The store is the source
of truth for the dynamic part of `ScenarioCatalog` until P5 introduces a SQLite
history layer.

Hardening (all dependency-free):
- Atomic writes: serialize to a temp file, fsync, then ``os.replace`` so a crash
  mid-write never corrupts the live file.
- Concurrency: an in-process lock plus an exclusive cross-process lockfile (never
  proceeds unlocked) guard the read-merge-write cycle; ``replace_with_retry``
  absorbs Windows sharing/permission races on ``os.replace``.
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
from chaosgen.storage.atomic_io import (
    ExclusiveFileLock,
    read_text_with_retry,
    replace_with_retry,
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
            # MODIFIED: transient Windows sharing races are not corruption —
            # retry before letting the caller fall back to the .bak file.
            raw = json.loads(read_text_with_retry(self.path))
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
                # MODIFIED: retry reads on Windows lock races; do not quarantine mid-append
                records = self._load_for_append()
                records.append(record)
                self._write(records)

    def delete_by_name(self, name: str) -> bool:
        """Remove a promoted scenario by name. Returns True if something was deleted."""
        # --- START MODIFICATION ---
        # Catalog GUI CRUD — delete only touches the promoted store, never builtins.
        target = (name or "").strip()
        if not target:
            return False
        with _GLOBAL_LOCK:
            with self._file_lock():
                records = self._load_for_append()
                kept = [r for r in records if r.name != target]
                if len(kept) == len(records):
                    return False
                self._write(kept)
                return True
        # --- END MODIFICATION ---

    def update_by_name(
        self,
        name: str,
        *,
        new_name: str | None = None,
        description: str | None = None,
        acceptance_criteria: Dict[str, Any] | None = None,
        clear_acceptance: bool = False,
    ) -> bool:
        """Update mutable fields of a promoted record. Returns True if found."""
        # --- START MODIFICATION ---
        target = (name or "").strip()
        if not target:
            return False
        with _GLOBAL_LOCK:
            with self._file_lock():
                records = self._load_for_append()
                found = False
                updated: List[PromotedCatalogRecord] = []
                for record in records:
                    if record.name != target:
                        updated.append(record)
                        continue
                    found = True
                    data = record.model_dump(mode="json")
                    if new_name is not None and new_name.strip():
                        data["name"] = new_name.strip()
                    if description is not None:
                        data["description"] = description
                    if clear_acceptance:
                        data["acceptance_criteria"] = None
                    elif acceptance_criteria is not None:
                        data["acceptance_criteria"] = acceptance_criteria
                    updated.append(PromotedCatalogRecord.model_validate(data))
                if not found:
                    return False
                self._write(updated)
                return True
        # --- END MODIFICATION ---

    def _load_for_append(self) -> List[PromotedCatalogRecord]:
        """Load for append with short retries; avoid quarantine on transient locks."""
        last_exc: Optional[BaseException] = None
        for attempt in range(8):
            try:
                if not self.path.exists():
                    return []
                raw = json.loads(read_text_with_retry(self.path))
                return self._parse(raw)
            except (json.JSONDecodeError, PromotedStoreError) as exc:
                last_exc = exc
                break
            except OSError as exc:
                last_exc = exc
                time.sleep(0.02 * (attempt + 1))
        if last_exc is not None:
            logger.warning(
                "Append load retry exhausted (%s); falling back to safe recovery",
                last_exc,
            )
        return self.load_records_safe()

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
        replaced = False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(envelope, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            # --- START MODIFICATION ---
            # Windows-safe replace retries; never skip the exclusive lock.
            # --- END MODIFICATION ---
            if self.path.exists():
                shutil.copy2(self.path, self.bak_path)
            replace_with_retry(tmp_path, self.path)
            replaced = True
        finally:
            if not replaced and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    # -- recovery ----------------------------------------------------------

    def _attempt_backup_recovery(self) -> List[PromotedCatalogRecord]:
        # Quarantine the corrupt main file so a human can inspect it later.
        if self.path.exists():
            corrupt_path = self.path.with_name(
                f"{self.path.name}.corrupt.{int(time.time())}"
            )
            try:
                replace_with_retry(self.path, corrupt_path)
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

    def _file_lock(self, timeout: float = 30.0, poll: float = 0.05) -> ExclusiveFileLock:
        # MODIFIED: never proceed unlocked (was the cross-process lost-write hazard)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return ExclusiveFileLock(self.lock_path, timeout=timeout, poll=poll)


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
