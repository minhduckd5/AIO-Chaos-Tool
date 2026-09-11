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

## Backend tidy candidates (from Phase 0)

| Candidate | Status | Why | Careful of |
|---|---|---|---|
| Split / facade `ChaosOrchestrator` | Phase 2 | God-object FSM + inject + audit + CTK | Qt + CLI call sites |
| Consolidate dual LLM advisor paths | Phase 2 | `advisor/` vs `ml/llm_advisor.py` | Import cycles, tests |
| Explicit run store (`run_id`) for history | **Open bug on PySide6 today — not fixed on `main` / this branch** | History inserts `[STARTED]` then adds a new `[PASSED]/[FAILED]` line; `_seed_history` keeps orphan `[STARTED]` via `_is_session_history_text`. Evidence: [`experiments_view.py`](../chaosgen/gui/views/experiments_view.py) `_on_experiment_done` still only `insertItem` — does **not** update the STARTED row. Web blueprint must model `run_id`; **do not treat this as web-only design** — GUI demo path still shows the bug | History seed / journals / session list |
| Cross-process inject mutex (optional) | Phase 2+ | Dual-inject currently operational-only | Windows locks, false sense of safety |
| Catalog `requires:` tags (chaos_mesh / hpa) | Phase 2 | Lab mismatch FAILs | Schema + GUI CRUD batch |
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
