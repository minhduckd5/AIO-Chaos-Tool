# Phase 1 merge gate — `feat/api-layer-explore`

Do **not** merge to `main` until all of (a)(b)(c) pass. Explore branch does not lower the honesty bar.

## Hard merge requirements

| Gate | Requirement | Status on this branch |
|---|---|---|
| **(a)** | Full `pytest` green with `--cov-fail-under=70`; `chaosgen/api` **not** omitted from coverage | **PASS** — 2026-09-12 local run: `707 passed, 2 skipped`, TOTAL **72.33%**; `chaosgen/api/*` present in report (log: `scratch/phase1_gate_a_pytest.txt`). Skips = `tests/test_secrets.py` POSIX permission checks (`Permission checks skipped on Windows`) — **not** HITL/API. |
| **(b)** | Core E2E rehearsal under `scratch/prom-loki-fit/` equivalent or better than `main` | Pending |
| **(c)** | Manual GUI HITL `delete_pod` confirm (toast matches FSM) | Pending |

## Dual-inject honesty

- GUI process and `chaosgen api` process each own an in-memory `ChaosOrchestrator` / FSM.
- Shared disk: audit JSONL, promoted catalog, settings, journals — file locks only, not inject mutex.
- **Operational rule:** never dual-inject GUI + API against the lab. No cross-process inject lock in Phase 1.

## In-process API concurrency

- Mutating routes (`approve`, `reject-all`, `halt`) serialize on a process-scoped `threading.Lock`.
- Critical section for approve: `set_audit_context(path_used="api_hitl")` then `approve_and_run`.
- Orchestrator `_consumed_approvals` prevents double-inject of the same experiment name in one AI queue (pre-inject FAIL discards via `_abort_steady_state`).

## Audit / verdict readers

- `/v1/audit/recent` uses `AuditStore.read_events()` → `atomic_io.iter_jsonl` (A9).
- `/v1/verdict/last` uses `load_verdict_report` — no parallel JSONL parser.

## Demo-day checklist line (also paste into local `docs/demo-day-checklist.md` if present)

> Confirm **`chaosgen api` is NOT running** during defense demo unless the committee explicitly asks for the HTTP surface.

## CI / packaging

- Extra: `pip install -e ".[api]"` (`fastapi`, `uvicorn`).
- CI installs `.[dev,gui,api]` before pytest.

## Next — gates (b) and (c) (manual; required before merge to `main`)

### (b) E2E rehearsal subset (`scratch/prom-loki-fit/`)

Re-run on this branch; results must be **equivalent or better** than current `main`:

| Rehearsal | Evidence file (min) |
|---|---|
| Toast / steady-state honesty | `ai_approve_e2e_toast_ss*.json`, `demo_fail_ss_http_health.json` |
| Settings reload without restart | `settings_save_norestart_e2e.json` |
| Invalid-target behavior | `invalid_target_p0_fix.json` |
| Multi-approve audit | `multi_approve_audit_e2e.json` |

Also confirm anti-reapprove does not break multi-HITL of **different** scenario names in the same queue.

### (c) Manual GUI confirm

1. Start GUI only — **do not** run `chaosgen api` in parallel (dual-inject rule).
2. HITL Approve on lab `delete_pod` path (`inject.chaos_backend: delete_pod`).
3. Toast / OUTPUT matches FSM `PASS`/`FAIL`/`PARTIAL` (no false success).
4. Optional smoke: `chaosgen api` alone → `GET /health` + `GET /v1/status` (no inject) while GUI is idle.

When (b)/(c) evidence exists (JSON logs / screenshots), attach paths here and re-open review before merging.
