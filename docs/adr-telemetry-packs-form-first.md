# ADR: Form-First Telemetry Packs (Bounded Metrics + Loki)

**Status:** Accepted (amended)  
**Date:** 2026-09-07  
**Amended:** 2026-09-07 — Dual-Mode ingest (Default form-first + Custom Guided Live Discovery Beta)  
**Context:** Hardcoded `GOLDEN_SIGNAL_QUERIES` and hand-embedded PromQL in
`examples/registry-vm-settings.yaml` do not port across boutique / cAdvisor / OTel
label vocabularies. Unrestricted live Prometheus metric discovery would conflict with
[adr-multi-arch-form-profiles.md](adr-multi-arch-form-profiles.md) (heuristic
auto-discovery deferred for **architecture**). Thesis objective requires Prometheus
**and** Loki ingest. Operators also need an opt-in path for unmodeled workloads
without abandoning deterministic enterprise packs.

## Decision

1. **Declarative YAML packs** under `chaosgen/telemetry/packs/` (`boutique`,
   `cadvisor`, `loki_system`) define PromQL / LogQL metric queries.
2. **Dual-Mode ingest (Step 1 — Data Source):**
   - **Default mode (Form-First):** Strictly curated, declarative YAML packs
     (`boutique` + `cadvisor` + `loki_system`). Selection remains
     `ingest.telemetry_profile` + `ingest.extra_packs` + `ingest.scope_namespace`
     (Settings / settings.yaml). Deterministic, zero live-discovery overhead.
   - **Custom mode (Guided Live Discovery — Beta / Experimental):**
     Operator-initiated only. After Prometheus & Loki connection check succeeds,
     a background prefetch probes live APIs — Prometheus `label_values` /
     `metric_names` (`__name__`) and Loki `label_values` — then presents a
     guided checklist. Not the default path; not architecture auto-discovery.
3. **Loki is metric-shaped:** `LokiClient.query_metric_range()` returns the same
   `TimeSeries` model as Prometheus. Pack LogQL uses `rate`/`sum by (app)`.
   Raw log-line NLP stays out of scope.
4. **Canonical v1 preserved:** pack series map through existing
   `CanonicalFeatureMapper` aliases — no schema_version bump, no joblib wipe.
5. **Service label normalization:** pack-declared `service_label` is rewritten to
   canonical `service` before FeatureEngineer column naming
   (`pack__{signal}__{service}__{stat}`). Guided Custom selections use the same
   collector/`PackQuery` path and normalization.
6. **Prom/Loki step sync:** one `collect_range` call passes identical
   `start`/`end`/`step` to both backends.
7. **Legacy golden** remains when packs return empty and
   `ingest.allow_legacy_golden: true`.
8. **Heuristic guided catalog & best-effort typing (Custom):**
   - Live metric names are noise-filtered and classified into five golden buckets
     (`traffic`, `errors`, `latency`, `saturation`, `logs`) via regex heuristics.
   - Rate / histogram templates enforce a robust `[5m]` window to reduce `NaN`
     on slow scrape intervals (preferring pack-aligned windows over `[1m]`).
   - Automated **Suggested** tagging marks candidates that align with Default pack
     signal slots (capped at ~6 high-priority signals for UX).
   - Metric type inference (counter vs gauge vs histogram) is **best-effort regex**,
     not Prometheus type authority (`/api/v1/metadata` deferred to Phase 2).
9. **Ephemeral session overrides:**
   - Custom checklist ticks are transient and pass strictly in memory via
     `AnalysisRequest` (`ingest_mode="custom"`, `custom_queries=[...]`) for the
     current GUI Run only.
   - Disk settings (`~/.config/chaosgen/settings.yaml`) are **not** mutated by
     Custom selections or ingest-mode toggles.
   - Namespace for pack/`$namespace` substitution resolves from
     `settings.ingest.scope_namespace` → kube default → `"default"`; it is **not**
     an editable field on Telemetry Step 1 (change via Settings / Connect).
10. **Strict architectural boundaries:**
    - Guided discovery is bounded to **telemetry signal selection** only.
    - It does **not** perform cluster topology discovery, service-map inference,
      or architecture auto-classification — architecture remains Form-First per
      [adr-multi-arch-form-profiles.md](adr-multi-arch-form-profiles.md).

## Consequences

### Positive

- Settings files declare parameters, not query code (Default path).
- Deterministic lab/defense demos; new stacks = new YAML pack.
- Per-service RCA attribution works for Prom and Loki pack series.
- Custom (Beta) opens OSS compatibility for unmodeled workloads without
  sacrificing reproducibility of Default enterprise packs.

### Negative / accepted

- Operators must pick a pack for Default; unknown stacks still need a pack author
  for first-class CI/replay, or accept ephemeral Custom selections.
- LogQL metrics lose message-body detail (acceptable under bounded scope).
- Regex metric typing is an MVP heuristic; Prom `/api/v1/metadata` typing and
  save-as-pack persistence are Phase-2 backlog.
- Custom queries reside in RAM and are not persisted across sessions.

## References

- Plan (packs): `.cursor/plans/telemetry_pack_profiles_41bf08fb.plan.md`
- Plan (guided custom): `.cursor/plans/guided_custom_discovery_4f519d95.plan.md`
- Parent ADR: `docs/adr-multi-arch-form-profiles.md`
- Packs: `chaosgen/telemetry/packs/`
- Loader: `chaosgen/telemetry/pack_loader.py`
- Guided discovery: `chaosgen/telemetry/guided_discovery.py`
- Pipeline contract: `chaosgen/gui/analysis_pipeline.py` (`AnalysisRequest.ingest_mode`)
