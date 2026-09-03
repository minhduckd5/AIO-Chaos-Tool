# Oct acceptance: Gated Control Plane (Option B)

Single source of truth for October 2026 code hardening. Aligns implementation and tests with thesis positioning **Option B**.

Authority: this file + [`docs/thesis-glossary.md`](thesis-glossary.md) + `chaosgen/schemas/`.  
LaTeX thesis remains **frozen** until December polish (Abstract/Conclusion re-weight only).

## Contribution hierarchy (Option B)

| Layer | Role | Defense evidence |
|-------|------|------------------|
| **Primary** | Gated control plane + Expectation Verdict (anomaly ≠ harm validation) | N=2 PoC: PASS specificity 0%, FAIL detection 100% |
| **Organizing** | Unknown → Describe → Known (`ScenarioKnowledgeState`) | Registration form + schemas; not a catalog-size KPI |
| **Execution** | UCAL + HITL + blast radius + dead man's switch | Blocked namespaces / fail-safe before/during inject |
| **Design** | Multi-architecture | Title + future work; `DISCOVERY_ENABLED = False` today |

## Acceptance criteria G1–G7

| ID | Criterion | Pass when | Out of scope |
|----|-----------|-----------|--------------|
| **G1** | Mandatory HITL for AI/advisor-generated live inject | Advisor path enters `pending_approval`; inject only after explicit approve (CLI/GUI). No silent auto-inject in thesis/demo config. | Manual YAML suite paths may use `start_experiment` if documented as operator-owned |
| **G2** | Blast radius / namespace gate at **inject-time** | `BlastRadiusController.validate_experiment` runs on the path into inject (not only at scenario generate). Default blocked: `kube-system`, `monitoring`. | Perfect multi-selector pod accounting |
| **G3** | Dead man's switch on active fault | DMS started when inject window is live; cleanup on rollback/complete | Production multi-node heartbeat product |
| **G4** | FSM clarity | States include pending → steady_state_check → injecting → verifying / rollback → idle; transitions logged | Full OPA policy engine |
| **G5** | Verdict handoff; IF ≠ harm decision | Post-inject (or CTK journal) produces `ExpectationVerdictReport` (`PASS`/`FAIL`/`PARTIAL`); Isolation Forest spikes are not the harm decision | Calibrated detector benchmark |
| **G6** | Defense artifacts | Each lab run keeps CTK journal + verdict record compatible with Ch.4 format | Expanding N beyond demo needs |
| **G7** | Glossary / schema lock | New APIs use glossary terms; no synonym drift (`KNOWN` vs “learned catalog”) | Renaming historical log files |

## Code touchpoints

| Area | Path |
|------|------|
| Orchestrator FSM / HITL | `chaosgen/orchestrator.py` |
| Blast radius | `chaosgen/safety/governance.py` |
| Dead man's switch | `chaosgen/safety/monitor.py` |
| Expectation Verdict | `chaosgen/evaluation/expectation_verdict.py`, `ctk_verdict.py` |
| Generate-time safety (secondary) | `chaosgen/advisor/scenario_generator.py` |
| Schemas | `chaosgen/schemas/incidents.py`, `scenarios.py` |

## Existing tests (baseline)

- `tests/test_orchestrator_hitl.py`
- `tests/test_safety_governance.py`
- `tests/test_safety_monitor.py`
- `tests/test_ctk_journal_verdict.py` (and related evaluation tests)

## Artifact rule (defense / Dec)

Prefer the Ch.4 pair:

1. CTK journal under `scratch/ctk/journals/` (or documented path)
2. Alignment / verdict docs under `docs/anomaly-verdict-alignment-*` or persisted `ExpectationVerdictReport` JSON

Canonical demo runs: `flagship-multi-kill` (`PASS`), `fail-redis-cart` (`FAIL`).

## Oct non-goals

- Filling `KNOWN` catalog for “self-learning complete”
- Enabling multi-arch discovery
- Thesis LaTeX rewrite
- Overfull `\hbox` polish
