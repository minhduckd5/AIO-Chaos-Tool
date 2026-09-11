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

| Candidate | Why | Careful of |
|---|---|---|
| Split / facade `ChaosOrchestrator` | God-object FSM + inject + audit + CTK | Qt + CLI call sites |
| Consolidate dual LLM advisor paths | `advisor/` vs `ml/llm_advisor.py` | Import cycles, tests |
| Explicit run store (run_id) for history | GUI STARTED orphan debt | History seed / journals |
| Cross-process inject mutex (optional) | Dual-inject currently operational-only | Windows locks, false sense of safety |
| Catalog `requires:` tags (chaos_mesh / hpa) | Lab mismatch FAILs | Schema + GUI CRUD batch |

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
