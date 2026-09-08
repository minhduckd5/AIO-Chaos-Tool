"""
Guided live discovery for Telemetry Custom mode (Beta / MVP).

Probe Prom/Loki label APIs, filter noise, wrap best-effort PromQL/LogQL templates
([5m] rate windows), and mark Suggested when candidates align with Default
enterprise pack signal slots (traffic/errors/latency/saturation/logs).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

from chaosgen.telemetry.pack_loader import PackQuery, ResolvedPackQueries

logger = logging.getLogger(__name__)

Bucket = Literal["traffic", "errors", "latency", "saturation", "logs"]
RATE_WINDOW = "5m"

_DROP_PREFIXES = (
    "go_",
    "process_",
    "promhttp_",
    "python_gc_",
    "python_",
    "kubelet_",
    "apiserver_",
    "etcd_",
    "coredns_",
    "node_exporter_",
)

_KEEP_RE = re.compile(
    r"(http|grpc|rpc|request|call|error|fail|latency|duration|container_cpu|"
    r"container_memory|container_network|throttl|working_set|receive_bytes|"
    r"transmit_bytes|cfs)",
    re.IGNORECASE,
)

_TRAFFIC_RE = re.compile(
    r"(request|call|http_req|rpc_.*handled|server_.*count).*(total|count)?$|"
    r"(requests|calls)_total$|http_requests_total|rpc_server_duration_count",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(
    r"(error|fail|fault|5xx|status_code_error|drop).*|"
    r"http_requests_total.*5|errors?_total",
    re.IGNORECASE,
)
_LATENCY_RE = re.compile(
    r"(latency|duration).*(_bucket|_seconds_bucket)|_bucket$",
    re.IGNORECASE,
)
_SATURATION_RE = re.compile(
    r"container_cpu|container_memory|cfs_throttl|working_set|"
    r"container_network_(receive|transmit)_bytes",
    re.IGNORECASE,
)

# Pack-aligned Suggested: enterprise Default signal families (tight — avoid ops noise)
_PACK_ALIGNED: dict[Bucket, tuple[re.Pattern[str], ...]] = {
    "traffic": (
        re.compile(r"^boutique:http_requests", re.I),
        re.compile(r"(^|_)http_requests_total$", re.I),
        re.compile(r"http_server_requests(_seconds)?_(count|total)$", re.I),
        re.compile(r"rpc_server_(duration_count|handled_total|request_size_count)$", re.I),
        re.compile(r"traces_spanmetrics_calls_total$|grpc_server_handled_total$", re.I),
    ),
    "errors": (
        re.compile(r"^boutique:http_errors|^boutique:http_error_ratio", re.I),
        re.compile(r"http_.*errors?_total$|errors?_total$|status_code_error", re.I),
        re.compile(r"traces_spanmetrics_calls_total.*STATUS_CODE_ERROR|span.*error", re.I),
    ),
    "latency": (
        re.compile(r"^boutique:http_latency", re.I),
        re.compile(r"http_request_duration.*_bucket$|rpc_server_duration_bucket$", re.I),
        re.compile(r"http_server_requests_seconds_bucket$", re.I),
    ),
    "saturation": (
        re.compile(r"container_cpu_usage_seconds_total|container_memory_working_set_bytes", re.I),
        re.compile(r"container_cpu_cfs_throttled|container_network_(receive|transmit)_bytes_total", re.I),
    ),
    "logs": (
        re.compile(r"loki_error_rate|log_error", re.I),
    ),
}

# Never mark Suggested for control-plane / exporter / node noise (still listable in Custom)
_SUGGEST_DENY_RE = re.compile(
    r"^(alertmanager_|prometheus_|node_|grafana_|etcd_|coredns_|"
    r"apiserver_|kubelet_|kube_proxy|netdata_|windows_)",
    re.I,
)

_SERVICE_LABEL_PREF = ("service_name", "service", "app", "pod", "container_name")
_BUCKET_ORDER: tuple[Bucket, ...] = (
    "traffic",
    "errors",
    "latency",
    "saturation",
    "logs",
)
_MAX_SUGGESTED = 6


@dataclass(frozen=True)
class DiscoveredQuery:
    """One candidate query for Custom checklist / collector."""

    id: str
    bucket: Bucket
    metric: str
    query: str
    source: Literal["prometheus", "loki"]
    suggested: bool
    service_label: str
    display_name: str = ""

    @property
    def metric_name(self) -> str:
        return f"pack__{self.id}"


@dataclass
class GuidedCatalog:
    """Result of a guided probe + suggest pass."""

    queries: list[DiscoveredQuery] = field(default_factory=list)
    service_label: str = "service"
    prom_services: list[str] = field(default_factory=list)
    loki_apps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stack_summary: str = ""

    @property
    def suggested(self) -> list[DiscoveredQuery]:
        return [q for q in self.queries if q.suggested]


def resolve_scope_namespace(settings: Any | None) -> str:
    """settings.ingest.scope_namespace → connect.kubernetes.default_namespace → default."""
    if settings is None:
        return "default"
    ingest = getattr(settings, "ingest", None)
    ns = getattr(ingest, "scope_namespace", None) if ingest is not None else None
    if isinstance(ns, str) and ns.strip():
        return ns.strip()
    connect = getattr(settings, "connect", None)
    k8s = getattr(connect, "kubernetes", None) if connect is not None else None
    kube_ns = getattr(k8s, "default_namespace", None) if k8s is not None else None
    if isinstance(kube_ns, str) and kube_ns.strip():
        return kube_ns.strip()
    return "default"


def keep_metric_name(name: str) -> bool:
    lower = name.lower()
    if any(lower.startswith(p) for p in _DROP_PREFIXES):
        return False
    return bool(_KEEP_RE.search(name))


def classify_bucket(metric: str) -> Bucket | None:
    if _LATENCY_RE.search(metric):
        return "latency"
    if _ERROR_RE.search(metric) and "duration" not in metric.lower():
        return "errors"
    if _SATURATION_RE.search(metric):
        return "saturation"
    if _TRAFFIC_RE.search(metric):
        return "traffic"
    if _KEEP_RE.search(metric):
        # Generic keep → traffic if request-like else saturation
        if re.search(r"cpu|mem|network|byte", metric, re.I):
            return "saturation"
        return "traffic"
    return None


def is_pack_aligned(metric: str, bucket: Bucket) -> bool:
    if _SUGGEST_DENY_RE.search(metric):
        return False
    patterns = _PACK_ALIGNED.get(bucket, ())
    return any(p.search(metric) for p in patterns)


def _suggest_preference(q: DiscoveredQuery) -> tuple:
    """Lower tuple sorts first — prefer boutique / Loki / cAdvisor over loose HTTP."""
    m = q.metric.lower()
    if q.source == "loki" or m.startswith("loki_"):
        rank = 0
    elif m.startswith("boutique:"):
        rank = 1
    elif m.startswith("container_"):
        rank = 2
    elif m.startswith("rpc_server") or "grpc_server" in m:
        rank = 3
    elif "http_request" in m or "http_server_request" in m:
        rank = 4
    else:
        rank = 5
    return (rank, m)


def finalize_suggested(
    queries: Sequence[DiscoveredQuery],
    *,
    max_suggested: int = _MAX_SUGGESTED,
) -> list[DiscoveredQuery]:
    """
    Cap ★ Suggested to ~max_suggested with bucket diversity (round-robin).

    Root cause addressed: alphabetical traffic-only cap selected alertmanager /
    node_disk / prometheus_http ahead of RED+USE coverage.
    """
    candidates: dict[Bucket, list[DiscoveredQuery]] = {b: [] for b in _BUCKET_ORDER}
    for q in queries:
        if q.suggested:
            candidates[q.bucket].append(q)
    for b in candidates:
        candidates[b].sort(key=_suggest_preference)

    keep_ids: set[str] = set()
    while len(keep_ids) < max_suggested:
        progressed = False
        for b in _BUCKET_ORDER:
            for q in candidates[b]:
                if q.id not in keep_ids:
                    keep_ids.add(q.id)
                    progressed = True
                    break
            if len(keep_ids) >= max_suggested:
                break
        if not progressed:
            break

    out: list[DiscoveredQuery] = []
    for q in queries:
        if q.suggested and q.id not in keep_ids:
            out.append(
                DiscoveredQuery(
                    id=q.id,
                    bucket=q.bucket,
                    metric=q.metric,
                    query=q.query,
                    source=q.source,
                    suggested=False,
                    service_label=q.service_label,
                    display_name=q.display_name,
                )
            )
        else:
            out.append(q)
    return out


def pick_service_label(
    *,
    available_labels: Sequence[str] | None = None,
) -> str:
    """Choose identity label from known preference order."""
    avail = set(available_labels or [])
    for cand in _SERVICE_LABEL_PREF:
        if cand in avail:
            return cand
    return "service"


def wrap_promql(metric: str, bucket: Bucket, service_label: str | None) -> str:
    """Best-effort template; prefer [5m] rate windows."""
    svc = service_label if service_label else None
    by_clause = f" by ({svc})" if svc else ""

    if metric.endswith("_bucket") or bucket == "latency":
        # Prefer histogram quantile when name looks like a bucket metric
        m = metric if metric.endswith("_bucket") else metric
        if not m.endswith("_bucket") and "duration" in m.lower():
            m = f"{m}_bucket" if not m.endswith("_bucket") else m
        if svc:
            return (
                f"histogram_quantile(0.95, sum by (le, {svc}) "
                f"(rate({metric}[{RATE_WINDOW}])))"
            )
        return (
            f"histogram_quantile(0.95, sum by (le) "
            f"(rate({metric}[{RATE_WINDOW}])))"
        )

    if re.search(r"_total$|_count$|requests_total|errors_total", metric, re.I) or bucket in (
        "traffic",
        "errors",
    ):
        if metric.endswith("_bytes") and "container_memory" in metric:
            pass  # fall through to gauge
        else:
            return f"sum{by_clause} (rate({metric}[{RATE_WINDOW}]))"

    # Gauge-like / saturation
    if re.search(r"_bytes$|working_set|MemAvailable|usage_percent", metric, re.I):
        return f"sum{by_clause} ({metric})"

    # Default: rate if *_total-ish else sum
    if metric.endswith("_total") or metric.endswith("_count"):
        return f"sum{by_clause} (rate({metric}[{RATE_WINDOW}]))"
    return f"sum{by_clause} ({metric})"


def wrap_loki_log_error(namespace: str, apps: Sequence[str]) -> str:
    ns = namespace or "default"
    if apps:
        # Limit regex size
        joined = "|".join(re.escape(a) for a in list(apps)[:40])
        selector = f'{{namespace="{ns}", app=~"{joined}"}}'
    else:
        selector = f'{{namespace="{ns}", app=~".+"}}'
    return (
        f"sum by (app) (rate({selector} "
        f'|~ "(?i)error|fatal|panic|critical" [{RATE_WINDOW}]))'
    )


def build_catalog_from_names(
    metric_names: Sequence[str],
    *,
    namespace: str = "default",
    service_label: str = "service",
    loki_apps: Sequence[str] | None = None,
    include_loki: bool = True,
    max_per_bucket: int = 40,
) -> GuidedCatalog:
    """Pure function: names → catalog (unit-testable without HTTP)."""
    warnings: list[str] = []
    by_bucket: dict[Bucket, list[DiscoveredQuery]] = {
        "traffic": [],
        "errors": [],
        "latency": [],
        "saturation": [],
        "logs": [],
    }

    kept = [m for m in metric_names if keep_metric_name(m)]
    dropped = len(metric_names) - len(kept)
    if dropped:
        warnings.append(f"Filtered {dropped} noisy/system metrics")

    for metric in kept:
        bucket = classify_bucket(metric)
        if bucket is None:
            continue
        # Prefer real bucket metrics for latency; skip bare duration without bucket
        if bucket == "latency" and not metric.endswith("_bucket"):
            if "bucket" not in metric.lower():
                continue
        qid = re.sub(r"[^a-z0-9_]+", "_", metric.lower()).strip("_")[:80]
        suggested = is_pack_aligned(metric, bucket)
        query = wrap_promql(metric, bucket, service_label)
        dq = DiscoveredQuery(
            id=qid,
            bucket=bucket,
            metric=metric,
            query=query,
            source="prometheus",
            suggested=suggested,
            service_label=service_label or "service",
            display_name=metric,
        )
        by_bucket[bucket].append(dq)

    # Cap per bucket (prefer suggested first)
    queries: list[DiscoveredQuery] = []
    for bucket, items in by_bucket.items():
        items.sort(key=lambda x: (not x.suggested, x.metric))
        queries.extend(items[:max_per_bucket])

    if include_loki:
        apps = list(loki_apps or [])
        loki_q = DiscoveredQuery(
            id="log_error_rate",
            bucket="logs",
            metric="loki_error_rate",
            query=wrap_loki_log_error(namespace, apps),
            source="loki",
            suggested=True,
            service_label="app",
            display_name="Loki log error rate",
        )
        queries.append(loki_q)

    queries = finalize_suggested(queries, max_suggested=_MAX_SUGGESTED)

    summary_parts = []
    if any(q.bucket == "traffic" for q in queries):
        summary_parts.append("HTTP/RPC")
    if any(q.bucket == "saturation" for q in queries):
        summary_parts.append("cAdvisor")
    if any(q.source == "loki" for q in queries):
        summary_parts.append("Loki")
    stack = " + ".join(summary_parts) if summary_parts else "unknown"

    return GuidedCatalog(
        queries=queries,
        service_label=service_label or "service",
        prom_services=[],
        loki_apps=list(loki_apps or []),
        warnings=warnings,
        stack_summary=stack,
    )


def probe_guided_catalog(
    prometheus: Any,
    loki: Any | None = None,
    *,
    namespace: str = "default",
    max_metric_names: int = 5000,
) -> GuidedCatalog:
    """Live probe Prom (+ optional Loki) and build GuidedCatalog."""
    warnings: list[str] = []
    names, err = prometheus.metric_names(max_names=max_metric_names)
    if err:
        warnings.append(f"Prometheus metrics: {err}")
        names = []

    # Probe service-ish labels
    label_hits: list[str] = []
    for lab in _SERVICE_LABEL_PREF:
        vals, lerr = prometheus.label_values(lab)
        if lerr:
            continue
        if vals:
            label_hits.append(lab)

    service_label = pick_service_label(available_labels=label_hits)

    prom_services: list[str] = []
    if service_label:
        vals, _ = prometheus.label_values(service_label)
        prom_services = vals[:200]

    loki_apps: list[str] = []
    include_loki = loki is not None
    if loki is not None:
        apps, lerr = loki.label_values("app")
        if lerr:
            warnings.append(f"Loki apps: {lerr}")
            include_loki = False
        else:
            loki_apps = apps
            # Intersect preference for log selector when both present
            if prom_services:
                inter = sorted(set(prom_services) & set(loki_apps))
                if inter:
                    loki_apps = inter

    catalog = build_catalog_from_names(
        names,
        namespace=namespace,
        service_label=service_label,
        loki_apps=loki_apps,
        include_loki=include_loki,
    )
    catalog.prom_services = prom_services
    catalog.loki_apps = loki_apps
    catalog.warnings = warnings + list(catalog.warnings)
    return catalog


def discovered_to_pack_queries(
    selected: Sequence[DiscoveredQuery],
) -> ResolvedPackQueries:
    """Map selected discovery rows → ResolvedPackQueries for TelemetryCollector."""
    if not selected:
        raise ValueError("Select at least one metric to proceed")
    queries: list[PackQuery] = []
    for dq in selected:
        queries.append(
            PackQuery(
                signal=dq.id,
                source=dq.source,
                query=dq.query,
                pack_id="guided",
                service_label=dq.service_label or "service",
                metric_name=dq.metric_name,
            )
        )
    return ResolvedPackQueries(queries=queries, pack_ids=["guided"])


def discovered_from_dicts(rows: Sequence[dict[str, Any]]) -> list[DiscoveredQuery]:
    """Rehydrate DiscoveredQuery list from AnalysisRequest session payloads."""
    out: list[DiscoveredQuery] = []
    for row in rows:
        out.append(
            DiscoveredQuery(
                id=str(row["id"]),
                bucket=row.get("bucket", "traffic"),  # type: ignore[arg-type]
                metric=str(row.get("metric", row["id"])),
                query=str(row["query"]),
                source=row.get("source", "prometheus"),  # type: ignore[arg-type]
                suggested=bool(row.get("suggested", False)),
                service_label=str(row.get("service_label") or "service"),
                display_name=str(row.get("display_name") or row.get("metric") or ""),
            )
        )
    return out
