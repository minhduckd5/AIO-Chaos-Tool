"""
Load and compose form-first telemetry packs (PromQL / LogQL metric queries).

No live Prometheus label discovery — packs are declarative YAML under
``chaosgen/telemetry/packs/``. Series identity labels are normalized to a
canonical ``service`` key before FeatureEngineer column naming.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Iterable, Literal, Mapping

import yaml

logger = logging.getLogger(__name__)

PACKS_DIR = Path(__file__).resolve().parent / "packs"
CANONICAL_SERVICE_KEY = "service"
_SIGNAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_KNOWN_SOURCES = frozenset({"prometheus", "loki"})


@dataclass(frozen=True)
class PackQuery:
    """One resolved pack signal ready for collector execution."""

    signal: str
    source: Literal["prometheus", "loki"]
    query: str
    pack_id: str
    service_label: str
    metric_name: str  # pack__{signal}


@dataclass
class ResolvedPackQueries:
    """Composed queries from one or more packs (later packs override same signal)."""

    queries: list[PackQuery] = field(default_factory=list)
    pack_ids: list[str] = field(default_factory=list)

    @property
    def prometheus(self) -> list[PackQuery]:
        return [q for q in self.queries if q.source == "prometheus"]

    @property
    def loki(self) -> list[PackQuery]:
        return [q for q in self.queries if q.source == "loki"]


def list_available_packs(packs_dir: Path | None = None) -> list[str]:
    root = packs_dir or PACKS_DIR
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.yaml") if p.is_file())


def resolve_packs(
    profile: str,
    *,
    extra_packs: Iterable[str] | None = None,
    namespace: str = "default",
    packs_dir: Path | None = None,
) -> ResolvedPackQueries:
    """
    Load ``profile`` then ``extra_packs`` (in order). Duplicate signal names:
    later packs win.
    """
    root = packs_dir or PACKS_DIR
    available = set(list_available_packs(root))
    ordered: list[str] = []
    for pack_id in [profile, *(extra_packs or ())]:
        pid = (pack_id or "").strip()
        if not pid:
            continue
        if pid not in available:
            raise ValueError(
                f"Unknown telemetry pack {pid!r}; available: {sorted(available)}"
            )
        if pid not in ordered:
            ordered.append(pid)

    if not ordered:
        raise ValueError("telemetry_profile must name at least one pack")

    scope = {"namespace": namespace}
    by_signal: dict[str, PackQuery] = {}
    for pack_id in ordered:
        loaded = _load_pack_file(root / f"{pack_id}.yaml", scope)
        for q in loaded:
            by_signal[q.signal] = q

    return ResolvedPackQueries(
        queries=list(by_signal.values()),
        pack_ids=ordered,
    )


def normalize_series_service_label(
    labels: Mapping[str, str],
    service_label: str,
) -> dict[str, str]:
    """
    Copy *labels* and set canonical ``service`` from ``service_label``.

    Prefer the pack-declared key; fall back to common aliases so mixed stacks
    still attribute. Empty identity is omitted (cluster-wide series).
    """
    out = {str(k): str(v) for k, v in labels.items()}
    candidates = (
        service_label,
        "service_name",
        "service",
        "app",
        "pod",
        "container_name",
        "destination_workload",
    )
    identity: str | None = None
    for key in candidates:
        raw = out.get(key)
        if raw and str(raw).strip():
            identity = str(raw).strip()
            break
    if identity:
        out[CANONICAL_SERVICE_KEY] = identity
    return out


def _load_pack_file(path: Path, scope: Mapping[str, str]) -> list[PackQuery]:
    if not path.is_file():
        raise ValueError(f"Telemetry pack file missing: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Pack {path.name} must be a YAML mapping")

    pack_id = str(raw.get("id") or path.stem).strip()
    if pack_id != path.stem:
        logger.warning(
            "Pack id %r differs from filename stem %r; using filename",
            pack_id,
            path.stem,
        )
        pack_id = path.stem

    service_label = str(raw.get("service_label") or "service").strip() or "service"
    signals = raw.get("signals")
    if not isinstance(signals, dict) or not signals:
        raise ValueError(f"Pack {pack_id!r} requires a non-empty 'signals' map")

    queries: list[PackQuery] = []
    for signal, spec in signals.items():
        if not _SIGNAL_NAME_RE.match(str(signal)):
            raise ValueError(
                f"Pack {pack_id!r}: invalid signal name {signal!r} "
                "(expected snake_case)"
            )
        if not isinstance(spec, dict):
            raise ValueError(f"Pack {pack_id!r} signal {signal!r} must be a mapping")

        source = str(spec.get("source") or "").strip().lower()
        if source not in _KNOWN_SOURCES:
            raise ValueError(
                f"Pack {pack_id!r} signal {signal!r}: source must be "
                f"'prometheus' or 'loki', got {source!r}"
            )

        query_key = "promql" if source == "prometheus" else "logql"
        query_raw = spec.get(query_key)
        if not isinstance(query_raw, str) or not query_raw.strip():
            raise ValueError(
                f"Pack {pack_id!r} signal {signal!r}: missing non-empty {query_key}"
            )

        query = _substitute_scope(query_raw.strip(), scope)
        queries.append(
            PackQuery(
                signal=str(signal),
                source=source,  # type: ignore[arg-type]
                query=query,
                pack_id=pack_id,
                service_label=service_label,
                metric_name=f"pack__{signal}",
            )
        )

    return queries


def _substitute_scope(template: str, scope: Mapping[str, str]) -> str:
    """Replace ``$namespace`` (and future scope vars) via string.Template."""
    # Allow bare $namespace without braces; escape other $ that are not scope keys.
    safe_scope = {k: str(v) for k, v in scope.items()}
    try:
        return Template(template).safe_substitute(safe_scope)
    except ValueError as exc:
        raise ValueError(f"Invalid pack query template: {exc}") from exc


def pack_ids_from_settings(ingest: Any) -> tuple[str, list[str], str]:
    """Extract profile / extras / namespace from IngestSettings-like object."""
    profile = str(getattr(ingest, "telemetry_profile", "boutique") or "boutique")
    extras = list(getattr(ingest, "extra_packs", None) or [])
    namespace = str(getattr(ingest, "scope_namespace", "default") or "default")
    return profile, extras, namespace


# --- START MODIFICATION ---
# GUI combo keys ↔ ingest profile/extras (Settings + Telemetry session override)
_PACK_COMBO_FULL = "boutique+cadvisor+loki"
_PACK_COMBO_CADVISOR = "boutique+cadvisor"
_PACK_COMBO_CORE = "boutique"


def ingest_to_pack_combo_key(profile: str, extra_packs: Iterable[str] | None) -> str:
    extras = {(p or "").strip() for p in (extra_packs or ()) if (p or "").strip()}
    if "cadvisor" in extras and "loki_system" in extras:
        return _PACK_COMBO_FULL
    if "cadvisor" in extras:
        return _PACK_COMBO_CADVISOR
    return (profile or "").strip() or _PACK_COMBO_CORE


def pack_combo_key_to_ingest(combo_key: str) -> tuple[str, list[str]]:
    key = (combo_key or "").strip() or _PACK_COMBO_CORE
    if key == _PACK_COMBO_FULL:
        return "boutique", ["cadvisor", "loki_system"]
    if key == _PACK_COMBO_CADVISOR:
        return "boutique", ["cadvisor"]
    return "boutique", []


def format_pack_status_line(
    profile: str,
    extra_packs: Iterable[str] | None,
    namespace: str,
    *,
    packs_dir: Path | None = None,
) -> str:
    """Human-readable strip: Active pack | ns | N queries mapped."""
    extras = [p for p in (extra_packs or []) if p]
    try:
        resolved = resolve_packs(
            profile, extra_packs=extras, namespace=namespace or "default", packs_dir=packs_dir
        )
        n_queries = len(resolved.queries)
    except ValueError:
        n_queries = 0
    extra_note = f" (+{len(extras)} packs)" if extras else ""
    return (
        f"Active: {profile}{extra_note} | ns: {namespace or 'default'} | "
        f"{n_queries} queries mapped"
    )
# --- END MODIFICATION ---
