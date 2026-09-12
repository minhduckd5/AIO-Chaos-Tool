# Phase 2 backlog (after thesis defense)

**Do not execute during thesis writing.** This file is a parking lot fed by Phase 0 evidence.  
Open a **new Plan mode** after Phase 1 merges to `main` with gates (a)(b)(c) passed.

**Inputs for that future plan:** this file + [`current-state.md`](current-state.md) + [`web-ui-layout-blueprint.md`](web-ui-layout-blueprint.md).  
**Do not** re-analyze architecture from zero.

---

## Goals

1. Backend tidy (reduce orchestrator concentration / clear ownership) **without** breaking PySide6.
2. Choose frontend stack (React/Vue/Svelte/… + TS/CSS tooling) based on **outsource team skills** at that time.
3. Build self-hosted SPA consuming `/v1/*` (prefer same-origin static+API like Versus; Electron only if product need).
4. Keep honesty bar: Plan → code → JSON / E2E evidence; no dual-inject lies.

---

## Phase 2A Completed Deliverables (September 2026)

- [x] **Explicit run store (`run_id`) for history:** Implemented `chaosgen/storage/run_resolver.py` and `GET /v1/experiments/history` to aggregate audit events into unified run records with terminal outcomes (`PASS`, `FAIL`, `PARTIAL`, `HALTED`), resolving the orphan `[STARTED]` bug for web consumers.
- [x] **Orphan resource tracking & sweep (`.chaosgen/orphan_resources.json`):** Implemented `chaosgen/storage/orphan_store.py` with `ExclusiveFileLock` and `POST /v1/control/orphan-sweep` emitting `orphan_sweep` audit events.
- [x] **REST Route Inventory:** Implemented `GET /v1/catalog`, `POST /v1/catalog/{name}/queue` (with 409 guard), `GET/PUT /v1/settings` (with active experiment 409 guard and masked credentials), `GET /v1/audit/summary` (counted filter tabs), `POST /v1/experiments/run` (operator direct hatch with blast radius pre-validation), and `POST /v1/telemetry/analyze` (symmetrical 409 guard).
- [x] **Read-only SSE Event Stream:** Implemented `GET /v1/events/stream` streaming FSM state transitions, active run status, and ping heartbeats.
- [x] **React 18 + Vite + Tailwind Single-Page Application:** Built self-hosted SPA under `web/` inspired by Versus industrial incident console design:
  - TopBar compact Honesty Pill (`idle`, `pending_approval`, `injecting`, `rollback` + last outcome + Prom/Loki live connectivity).
  - Grouped Sidebar (Operations vs Observation).
  - 5-Column HITL Table with Origin badges (`AI Advisor` vs `Catalog`) and "Arm Injection — This is Not a Drill" confirmation modal.
  - Scenario Catalog with Amber 409 conflict replacement modal (`?force=true`).
  - Experiments Console with Emergency HALT button.
  - Audit Incident Timeline with counted filter tabs and 2-column Detail Modal (Narrative vs Hard Facts).
  - Evaluation Alignment view and Settings view.
- [x] **PySide6 Lifecycle Policy:** Formally archived in-place under `extras_require["gui"]` documented in `architecture/pyside6-lifecycle-policy.md`.
- [x] **Testing Rigor:** Automated unit & integration tests in `tests/test_api_sidecar.py` (30/30 passed) and Playwright E2E suite (`web/e2e/hitl-flow.spec.ts`).

---

## Backend tidy candidates (from Phase 0)

| Candidate | Status | Why | Careful of |
|---|---|---|---|
| Split / facade `ChaosOrchestrator` | Phase 2 | God-object FSM + inject + audit + CTK | Qt + CLI call sites |
| Consolidate dual LLM advisor paths | Phase 2 | `advisor/` vs `ml/llm_advisor.py` | Import cycles, tests |
| Explicit run store (`run_id`) for history | **Open bug on PySide6 today — not fixed on `main` / this branch** | History inserts `[STARTED]` then adds a new `[PASSED]/[FAILED]` line; `_seed_history` keeps orphan `[STARTED]` via `_is_session_history_text`. Evidence: [`experiments_view.py`](../chaosgen/gui/views/experiments_view.py) `_on_experiment_done` still only `insertItem` — does **not** update the STARTED row. Web blueprint must model `run_id`; **do not treat this as web-only design** — GUI demo path still shows the bug | History seed / journals / session list |
| Cross-process inject mutex (optional) | Phase 2+ | Dual-inject currently operational-only | Windows locks, false sense of safety |
| Catalog `requires:` tags (chaos_mesh / hpa) | Phase 2 | Lab mismatch FAILs | Schema + GUI CRUD batch |
| Orphan resource tracking & sweep (`.chaosgen/orphan_resources.json`) | Phase 2 | Recorded when rollback fails (e.g. CRD missing on cluster); accumulates silently without GC command or startup check | Disk growth, un-reverted cluster state |
| Multi-source queue composition (Catalog + AI Advisor) | Phase 2 (Forbidden in Phase 1) | Merging disparate sources in Phase 1 causes metadata mismatches (`AdvisorReport` anomaly context, `_experiment_db_ids`, anti-reapprove tokens). Phase 1 enforces deterministic replace semantics only. | Queue index mapping, stale report metadata, audit trail fidelity |
| discovery / bootstrap | **Not dead code** — keep | CLI `discover`/`bootstrap` + optional GUI when `DISCOVERY_ENABLED`; default False hides nav only | Do not delete as “unused” without checking CLI |

### History `[STARTED]` — known live GUI bug

- **Fixed on main?** No (as of `feat/api-layer-explore` tip / base `e121794`).
- **Symptom:** After experiment finishes, list still shows `[STARTED] …` plus a separate final line / journal badge.
- **Root cause (unchanged):** session stub preserved; finish handler does not mutate by `run_id`.
- **Demo impact:** cosmetic / confusing if committee clicks History; not a false inject outcome if Evaluation/toast are honest.
- **Fix timing:** prefer a small PySide6 patch on a dedicated branch or Phase 2; not required to unblock Phase 1 API scaffold, but must stay visible in backlog.

---

## Frontend (deferred stack pick)

- Hosting: **self-hosted SPA** first.
- IA already in `web-ui-layout-blueprint.md` (stack-agnostic).
- Decide stack when outsource roster is known; FastAPI JSON does not force a framework.
- Consider realtime needs (OUTPUT / audit) → WebSocket later, not Phase 1.

---

## Outsource handoff packet

Ship to external team:

1. `architecture/current-state.md`
2. `architecture/web-ui-layout-blueprint.md`
3. This backlog
4. OpenAPI from Phase 1 API (once merged)
5. `docs/demo-day-checklist.md` + lab limits (no Chaos Mesh CRDs; lab boutique criteria)
6. Rehearsal evidence under `scratch/prom-loki-fit/` as behavioural goldens
7. Explicit: PySide6 remains until product decision otherwise

---

## Re-rehearsal requirement

Any Phase 2 structural change requires re-running the core E2E set (toast/SS, settings-norestart, invalid-target, multi-approve-audit) plus GUI HITL `delete_pod` manual confirm — same bar as Phase 1 merge gate.

---

## Web UI Honesty Rehearsal Status (September 2026 - Unverified)

> **CRITICAL ARCHITECTURAL STATUS (Decision A+):**
> - **System of Record for Thesis Defense:** **PySide6 GUI (`chaosgen.gui.main`)** remains the sole verified, authoritative demo interface with 49+ automated checks and rigorous manual HITL rehearsal evidence.
> - **Web SPA (`web/`):** **FROZEN** in current read-mostly prototype state on `feat/api-layer-explore`. No new mutating routes or frontend features are to be added before thesis submission.
> - **Honesty Rehearsal Status on Web:** **UNVERIFIED / PENDING FULL REHEARSAL** (Explicitly **NOT** "Not-Applicable"). While backend API sidecar has 49/49 automated unit/integration tests passing, the Web UI has only completed a single happy-path PASS run (`frontend-pod-kill-verification`).
> - **Unverified Web UI Edge Cases (Technical Debt to be resolved post-defense):**
>   1. UI error toast/banner veracity when orchestrator encounters FSM state exceptions (`MachineError`).
>   2. Dual-approve race condition serialization across multiple browser sessions.
>   3. UI rendering of 422 Unprocessable Entity when an operator inputs an invalid target violating Blast Radius policies.
>   4. Dynamic SSE reconnection resilience when network partitions occur mid-run.
>   5. Settings reload mid-session consistency without application restart.
> 
> *Constraint:* No thesis claims regarding "completed web migration" or "production-grade web console" shall be made without executing the full 6-scenario rehearsal matrix against the Web SPA.
