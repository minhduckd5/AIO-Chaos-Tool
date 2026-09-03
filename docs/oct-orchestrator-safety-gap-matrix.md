# Oct orchestrator/safety gap matrix

Project: `H-Project-AIO-Chaos-Tool`  
Index: reindexed **2026-09-01** (moderate/full metadata; ~3065 nodes). `approve_and_run` graph lines **1005–1020** match disk.  
Acceptance: [`docs/oct-gated-control-plane-acceptance.md`](oct-gated-control-plane-acceptance.md)

## Hotspot summary

| ID | Status | Finding |
|----|--------|---------|
| G1 | **PASS (advisor path)** / **PARTIAL (direct run)** | `run_ai_experiment` → `pending_approval` → `approve_and_run`. Direct `run_experiment()` → `start_experiment` skips HITL (operator-owned YAML); documented as allowed if not AI auto-path. |
| G2 | **PASS with harden** | Generate-time: `ScenarioGenerator` calls `validate_experiment`. Inject-time: `_run_steady_state_check` (after approve **and** after `start_experiment`) also calls it. **Gap closed:** re-validate at start of `_execute_injection` (defense in depth / post-approve tamper). |
| G3 | **GAP → harden** | `DeadMansSwitch` only started when `steady_state_check` is truthy **and** validation succeeds. Empty check still proceeds to inject **without** DMS. |
| G4 | **PASS** | States: idle, pending_approval, steady_state_check, injecting, verifying, rollback. |
| G5 | **PASS** | CTK: `_evaluate_ctk_run` → `ExpectationVerdictReport`. Legacy: `_run_verification` → `evaluate_acceptance_detailed`. IF not used as harm decision. |
| G6 | **PASS (process)** | Journals + `docs/anomaly-verdict-alignment-*`; `scratch/` not in graph (by design). |
| G7 | **PASS** | Glossary Option B + schemas; Oct docs lock terms. |

## Call path (post-reindex + source)

```text
Advisor report
  → run_ai_experiment / submit_for_approval → pending_approval
  → approve_and_run → approve_experiment
  → steady_state_check (_run_steady_state_check)
       → BlastRadiusController.validate_experiment   [G2]
       → optional pod-count soft guard
       → validator.validate(steady_state)
       → DeadMansSwitch.start                        [G3]
  → check_passed → injecting (_execute_injection)
       → validate_experiment again                   [G2 harden]
       → UCAL / modules
  → verifying (_run_verification / CTK verdict)      [G5]
  → idle + _cleanup_safety (DMS stop)
```

Alternate: `run_experiment` → `start_experiment` → same `steady_state_check` (HITL skipped; G1 PARTIAL).

## Pre-harden hypothesis (Aug 26 stale graph)

Stale inbound edges suggested G2 only at generate-time. **Fresh source contradicts that** for the FSM inject path: line ~655 in `_run_steady_state_check`. Generate-time validation remains a second layer, not the only gate.

## Harden actions (Phase 2) — done 2026-09-01

1. **G2:** Re-validate in `_execute_injection`; abort/rollback on `ValueError`.
2. **G3:** Arm DMS whenever steady-state path succeeds; fall back to `_default_steady_state()`; warn if still missing.
3. **Tests:** Added in `tests/test_orchestrator_hitl.py` (`TestGatedInjectSafety`) — 18 related tests passed.

## Final Pass/Fail (after harden)

| ID | Result |
|----|--------|
| G1 | **PASS** (advisor HITL) / **PARTIAL** (`run_experiment` operator path) |
| G2 | **PASS** (generate + steady_state + inject re-check) |
| G3 | **PASS** (DMS armed on success path; default check fallback) |
| G4 | **PASS** |
| G5 | **PASS** |
| G6 | **PASS** (artifact process) |
| G7 | **PASS** |
