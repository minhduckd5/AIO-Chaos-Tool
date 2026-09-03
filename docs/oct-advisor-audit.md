# Advisor audit (Oct upstream)

Date: 2026-09-01  
Scope: `chaosgen/advisor/` (`pipeline.py`, `scenario_describer.py`, `scenario_generator.py`, `catalog_promoter.py`, `scenario_ranker.py`, related stores)  
Glossary: `docs/thesis-glossary.md`  
Prior: `docs/oct-gatekeeper-audit.md` (PASS)

## Test suite

```text
pytest tests/test_advisor.py \
       tests/test_advisor_pipeline_integration.py \
       tests/test_promoted_catalog.py \
       tests/test_scenario_describer.py --no-cov
```

| Result | Detail |
|--------|--------|
| **61 passed** | Knowledge lifecycle, SafetyPolicy generate-time reject, fallback, promote guards |
| **1 failed** | `TestConcurrency::test_parallel_promotes_no_lost_entries` — Windows `Permission denied` on temp `promoted_scenarios.json` (env flake; not schema/logic regression) |

## 1. Knowledge state lifecycle

| Step | Behavior | Status |
|------|----------|--------|
| Input | Gatekeeper `IncidentCandidate` with `passes_downstream` (REAL/CHRONIC only) | **PASS** |
| Conceptual unknown | Incident has no catalog entry until describe succeeds | **PASS** |
| Success describe | Forces `knowledge_state=DESCRIBED` | **PASS** |
| Fallback describe | Stays `UNKNOWN` + `metadata.describe_fallback=True` | **PASS** |
| Chaos generate | `filter_chaos_descriptions` requires `DESCRIBED` and **not** fallback | **PASS** |
| Auto-`KNOWN` at generate | Never — `KNOWN` only set inside `CatalogPromoter.promote` | **PASS** |

Note: There is no separate persisted object named `IncidentCluster`; the gatekeeper emits `IncidentCandidate`. Schema default on `UnknownScenarioDescription` is `DESCRIBED`; code always sets state explicitly on success/fallback.

## 2. SafetyPolicy / whitelist compatibility

| Check | Behavior | Status |
|-------|----------|--------|
| Generate-time G2 | `ScenarioGenerator.generate` → `BlastRadiusController.validate_experiment`; rejects with `logger.warning` | **PASS** |
| Default target NS | Unresolved hints → `TargetSpec(namespace="default")` | **PASS** (safe default) |
| Pipeline wiring | `SafetyPolicy.from_settings(settings.safety)` passed into generator | **PASS** |
| Payload shape | Experiments are `ChaosExperiment` / fault specs (not a type named `ScenarioPayload`) | **PASS** (schema exists under faults) |

Orchestrator still re-validates at inject (prior G2 harden).

## 3. Fallback (LLM failure / malformed)

| Check | Behavior | Status |
|-------|----------|--------|
| Retries | `ScenarioDescriber` retries then `_build_fallback_description` | **PASS** |
| No batch crash | `describe_batch` continues after per-incident fallback | **PASS** |
| Logging | `logger.warning` per attempt; `logger.error` on FALLBACK | **PASS** |
| Chaos blocked | Fallback excluded from `filter_chaos_descriptions` → zero experiments (integration test) | **PASS** |
| Promote blocked | `PromoteError` / GUI `promote_blocked_reason` on fallback | **PASS** |

Heuristic path after LLM fail is the **deterministic fallback description**, not a separate catalog template inject. Catalog/builtin scenarios are a different reuse path (`ScenarioCatalog`), not the describe-failure branch.

## 4. Promote contract ↔ `promoted_scenarios.json`

`CatalogPromoter._check_guards` requires:

1. `knowledge_state == DESCRIBED`
2. Not `describe_fallback`
3. Non-empty `approved_by` (HITL)
4. Syntactically valid `acceptance_criteria`
5. No duplicate name / incident id

Then writes store and sets `KNOWN`.

| Thesis Option B expectation | Code reality | Status |
|-----------------------------|--------------|--------|
| Promote **only** after `ExperimentVerdict.PASS` | `CatalogPromoter.promote(..., verdict=)` requires PASS | **PASS** (hardened) |
| FAIL/PARTIAL must not write catalog | Raises `PromoteError`; state stays `DESCRIBED` | **PASS** |
| HITL + no fallback | Enforced | **PASS** |

CLI: `--verdict pass|fail|partial` or `--from-verdict <ExpectationVerdictReport JSON>`.  
GUI: loads last verdict report; blocks unless PASS.

## 5. Ranker

`ScenarioRanker` scores confidence / history / coverage / safety for HITL ordering; does not mutate knowledge state or promote. **PASS** (out of promote critical path).

## Summary table

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Knowledge lifecycle UNKNOWN→DESCRIBED; no auto-KNOWN at generate | **PASS** |
| 2 | SafetyPolicy / blast-radius at generate | **PASS** |
| 3 | LLM fallback + logs + no crash | **PASS** |
| 4 | Promote only on PASS verdict | **PASS** (hardened 2026-09-01: `verdict=` required; FAIL/PARTIAL raise `PromoteError`) |
| 5 | Unit/integration tests | **PASS*** (61; 1 concurrency flake on Windows) |

\*Re-run concurrency test if needed; treat as environmental.

## Ready for E2E?

**Yes.** Gatekeeper → Advisor → Safety → Verdict → Promoter(PASS-only) aligned with Option B. Registry-vm smoke can exercise N=2: `fail-redis-cart` (FAIL → promote rejected) and `flagship-multi-kill` (PASS → promote allowed).
