"""Form-first telemetry pack resolution (PromQL / LogQL metric queries)."""

from chaosgen.telemetry.guided_discovery import (
    DiscoveredQuery,
    GuidedCatalog,
    build_catalog_from_names,
    discovered_from_dicts,
    discovered_to_pack_queries,
    keep_metric_name,
    probe_guided_catalog,
    resolve_scope_namespace,
)
from chaosgen.telemetry.pack_loader import (
    PackQuery,
    ResolvedPackQueries,
    format_pack_status_line,
    ingest_to_pack_combo_key,
    list_available_packs,
    normalize_series_service_label,
    pack_combo_key_to_ingest,
    resolve_packs,
)

__all__ = [
    "DiscoveredQuery",
    "GuidedCatalog",
    "PackQuery",
    "ResolvedPackQueries",
    "build_catalog_from_names",
    "discovered_from_dicts",
    "discovered_to_pack_queries",
    "format_pack_status_line",
    "ingest_to_pack_combo_key",
    "keep_metric_name",
    "list_available_packs",
    "normalize_series_service_label",
    "pack_combo_key_to_ingest",
    "probe_guided_catalog",
    "resolve_packs",
    "resolve_scope_namespace",
]
