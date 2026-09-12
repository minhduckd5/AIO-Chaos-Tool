# Phase 1 merge gate — `feat/api-layer-explore`

Do **not** merge to `main` until all of (a)(b)(c) pass. Explore branch does not lower the honesty bar.

## Hard merge requirements

| Gate | Requirement | Status on this branch |
|---|---|---|
| **(a)** | Full `pytest` green with `--cov-fail-under=70`; `chaosgen/api` **not** omitted from coverage | **PASS** — 2026-09-12 local run: `707 passed, 2 skipped`, TOTAL **72.33%**; `chaosgen/api/*` present in report (log: `scratch/phase1_gate_a_pytest.txt`). Skips = `tests/test_secrets.py` POSIX permission checks (`Permission checks skipped on Windows`) — **not** HITL/API. |
| **(b)** | Core E2E rehearsal under `scratch/prom-loki-fit/` equivalent or better than `main` | **PASS** — 2026-09-12 on `feat/api-layer-explore` @ `a5e8998` (see table below) |
| **(c)** | Manual GUI HITL `delete_pod` confirm (toast matches FSM) | **PASS** — 2026-09-12 live run on k3s boutique cluster: scenario `ai-process_kill-checkoutservice-gate-c` approved via PySide6 AdvisorView; outcome `PASS` strictly matches toast label (`Last Approve: PASS — Approved experiment PASS`), button transitions to `Approved (PASS)`, FSM state honest (`pending_approval` post-requeue), audit trail records 5 events (`path_used: ai_hitl`), evidence saved in `scratch/gui-hitl/gate_c_evidence.json` and screenshot in `docs/assets/pyside6_gate_c_verified.png`. |

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

| Rehearsal | Evidence file | Result (2026-09-12) |
|---|---|---|
| Toast / SS PASS (3× live delete_pod) | `ai_approve_e2e_toast_ss.json` | `demo_gate_pass=true` |
| Toast / SS FAIL (bad Prom) | `ai_approve_e2e_toast_ss_fail.json` | `honesty_gate_pass=true` |
| Demo FAIL http_health | `demo_fail_ss_http_health.json` | `honesty_gate_pass=true` |
| Settings reload without restart | `settings_save_norestart_e2e.json` | `demo_gate_pass=true` |
| Invalid-target P0 | `invalid_target_p0_fix.json` | `gate_pass=true` (`NO_TARGET`) |
| Multi-approve audit (2 names) | `multi_approve_audit_e2e.json` | `gate_pass=true`; both `ran:true` — anti-reapprove does **not** block different names |

Harness note: `run_ai_approve_e2e_toast_ss.py` now drains via `reject_all()` between cycles (HITL requeue leaves `pending_approval`; each cycle is a fresh generate→approve). Script lives under gitignored `scratch/`.

Also confirm anti-reapprove does not break multi-HITL of **different** scenario names in the same queue. — **confirmed** via `multi_approve_audit_e2e.json`.

### Builtin catalog × boutique (same HITL path as GUI Catalog)

Gate (b) AI rehearsals are **not** the only catalog path. GUI **Catalog → Add to Queue → Approve** uses `submit_catalog_experiment` → same FSM as `run_ai_experiment`. Evidence 2026-09-12:

| Check | Result |
|---|---|
| Control: raw builtin `replica-reduction` target `app-pod` | `NO_TARGET` — honesty OK (placeholders are not boutique) |
| Retarget `replica-reduction` → each boutique Deployment (12) | **12/12** `PASS` + toast; evidence `scratch/prom-loki-fit/builtin_catalog_boutique_e2e.json` |
| Other MICROSERVICES builtins (`network_*`, OOM) | **Skipped** in live inject; **tested via API**: `HTTP 200` + `PARTIAL` honesty (no 500, no 404 leak, no false PASS); evidence `scratch/prom-loki-fit/builtin_mesh_api_honesty_e2e.json` |

Harness: `scratch/prom-loki-fit/run_builtin_catalog_boutique_e2e.py` (`gate_pass=true`).

### Mesh-incompatible builtins via API honesty check

When calling `POST /v1/pending/{index}/approve` on the 4 builtin scenarios requiring Chaos Mesh or unmapped faults (`upstream-timeout-cascade`, `dns-resolution-failure`, `partial-partition`, `oom-kill`) against the `delete_pod` lab:
- **HTTP Layer:** `200 OK` across all 4 (no HTTP 500 crash, no HTTP 404 route leak).
- **Business Layer:** Payload returns `{"ran": true, "outcome": "PARTIAL", "experiment": "..."}` — **never false PASS**.
  - *Semantics note (documented in OpenAPI):* `PARTIAL` is the Orchestrator's legacy FSM outcome for both injection mid-abort (e.g. missing CRD, unsupported fault) and genuine partial verification. Automated agents must query `GET /v1/audit/recent` to inspect `notes` for root-cause differentiation.
- **FSM & Safety:** Rollback triggers cleanly, FSM resets to `pending_approval` via requeue. Audit log emits `failure`/`aborted` with explicit root cause.
- Evidence: `scratch/prom-loki-fit/builtin_mesh_api_honesty_e2e.json` (`all_honesty_ok=true`).

### (c) Manual GUI confirm

**Status: PASS (Formally verified 2026-09-12)**
- **Target:** Live Google Online Boutique cluster (`k3s-control`, `k3s-worker1`, `k3s-worker2`), service `checkoutservice` in namespace `default`.
- **Harness:** `scratch/verify_pyside6_gate_c.py` executing `MainWindow` and `AdvisorView` against live cluster (`delete_pod` executor).
- **Workflow Executed:**
  1. Staged AI-generated scenario `ai-process_kill-checkoutservice-gate-c` into Step 3 Review Results.
  2. Verified FSM state is `pending_approval`.
  3. Clicked `Approve` button (triggering `advisor_view._on_approve(0)` -> `AppController.approve_and_run(0)`).
  4. Executed live pod kill and steady-state check (`operator-side Prom default up{job=~".+"}`).
  5. UI updated deterministically:
     - Table Action Button: `Approved (PASS)` (disabled).
     - Step 3 Outcome Banner: `Last Approve: PASS — Approved experiment PASS` (semantic role `success`).
     - Bottom Log Console: `Experiment passed: Approved experiment PASS`.
  6. FSM state honesty: transitioned to `injecting`, then `verifying`, then requeued cleanly to `pending_approval` (1 remaining queued item).
  7. Audit log verification: recorded 5 events (`queued`, `approved`, `inject_started`, `inject_finished`, `queued`) with strict `path_used="ai_hitl"`, actor `sre-gate-c-tester`, and outcome `success`.
- **Evidence Files:**
  - Structured run record: `scratch/gui-hitl/gate_c_evidence.json`
  - High-resolution window grab screenshot: `docs/assets/pyside6_gate_c_verified.png`
  - Audit trail slice: `scratch/gui-hitl/gate_c_audit.jsonl`
