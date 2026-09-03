# ADR: Form-First Multi-Architecture Profiles (Deferred Auto-Discovery)

**Status:** Accepted  
**Date:** 2026-09-02  
**Context:** Thesis form and GVHD negotiation require **multi-architecture** coverage (microservices, modular monolith, event-driven, monolith, client–server, serverless). Heuristic `ArchitectureClassifier` auto-discovery is incomplete, risky near defense (September 2026), and conflicts with explicit operator intent in production chaos practice.

## Decision

1. **Profile mode is form-first.** Operator selects `hints.architecture` + `hints.environment` via Settings GUI or CLI (`chaosgen config init`). `DISCOVERY_ENABLED` remains `False`.
2. **Static presets** in `chaosgen/config/profile_presets.py` supply `DiscoveryReport` skeletons (service map, edges, observability defaults). `resolve_discovery_report()` uses `build_profile_from_hints()` when architecture is set; otherwise falls back to microservices boutique default.
3. **Connect block** (`connect.kubernetes`, `connect.docker`, `connect.broker`, `connect.toxiproxy`) is validated on save per architecture (`profile_validation.py`).
4. **Priority matrix (thesis defense):**
   - **P0 live inject:** `microservices` (K8s / Chaos Toolkit + Chaos Mesh), `modular_monolith` (Docker Compose / Pumba).
   - **P1 catalog + dry-run:** `event_driven`, `monolith`, `client_server`, `serverless` — form load, catalog filter, and `inject.dry_run=true` generate path only.
5. **Observability probe** still runs when `hints.observability` is configured (automated reachability check); architecture topology is **not** inferred heuristically.
6. **Heuristic auto-discovery** (`ArchitectureClassifier`, `run_full_discovery`) is **deferred**, not removed — re-enable via `DISCOVERY_ENABLED=True` when corpus and lab coverage justify it.

## Rationale

| Alternative | Why rejected (now) |
|-------------|------------------|
| Full auto-discovery for 6 arch styles | High false-positive risk; lab-dependent; no time to validate before defense |
| Microservices-only thesis | Violates registered multi-arch scope and advisor Phase 2 requirement |
| Fake multi-arch (rename MS services) | No catalog/UCAL/inject differentiation; fails committee scrutiny |

Form-first aligns with chaos engineering practice: **experiments declare explicit blast radius and environment**; the control plane should not guess production topology.

## Consequences

### Positive

- Boutique microservices demo unchanged when `hints` are empty (safe default).
- Catalog, LLM suffixes, promoter, and UCAL route by `DiscoveryReport.architecture.type`.
- Modular monolith P0 path proved end-to-end: Profile → Pumba SIGKILL → Prometheus blackbox → Expectation verdict (see evidence below).
- P1 profiles unblock thesis narrative without requiring six full inject labs.

### Negative / accepted

- Operator must configure connect credentials; no “Scan System” magic button.
- P1 architectures do not prove live inject on defense day unless lab is extended later.
- Form labels must stay aligned with `scenario_catalog.py` target names per lab README.

## Evidence (September 2026)

| Artifact | Meaning |
|----------|---------|
| `examples/modular-monolith-settings.yaml` | P0 form-first settings |
| `labs/modular-monolith/` | Compose arena (no sidecar workarounds) |
| `docs/modular-monolith-live-fire.json` | Pumba kill + `probe_success` bleed timeline |
| `docs/modular-monolith-verdict-report.json` | **FAIL** — Docker Desktop did not auto-restart after `SIGKILL` despite `restart: always` |
| `docs/modular-monolith-verdict-rerun.json` | **PASS** after HITL rollback (`docker compose up -d`) |
| `scripts/demo_multi_arch_matrix.py` | Six-profile dry-run matrix for rehearsal |

### True-negative finding (defense narrative)

ChaosGen surfaced an **infrastructure blind spot**: `docker kill --signal SIGKILL` left `chaosgen-monolith-monolith-app-1` in `Exited (137)` with `RestartCount=0`. Prometheus blackbox correctly alarmed; operational verdict **FAIL** until human rollback. This demonstrates ROI beyond application-level faults — **orchestrator restart semantics must be validated, not assumed.**

## References

- Plan: `.cursor/plans/multi-arch_form-first_c72e2fb4.plan.md` (WS-1 … WS-6)
- Presets: `chaosgen/config/profile_presets.py`
- Resolver: `chaosgen/discovery/__init__.py` → `resolve_discovery_report()`
- Validation: `chaosgen/config/profile_validation.py`
