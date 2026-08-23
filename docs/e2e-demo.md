# P6 E2E Demo Guide

> Thesis defense demo — happy path + resilience beat. See [IT Project Proposal](IT_PROJECT_PROPOSAL.md) §7.3 G6.

## Prerequisites

- `pip install -e ".[dev]"`
- Lab Prometheus/Loki **or** offline export (`chaosgen analyze --export <path>`)
- `examples/demo-criteria.yaml` (adjust `http_health` for your stack)
- Ollama running for happy path; **stopped** for resilience beat

## Artifacts

| File | Purpose |
|------|---------|
| `examples/demo-criteria.yaml` | Promote acceptance criteria |
| `examples/demo-expectation-criteria.yaml` | P0-B operational claim + SLA expectations |
| `docs/advisor-demo-verdict-beat.md` | Advisor demo narration for verdict beat |
| `examples/demo-e2e.sh` | Happy path (bash) |
| `examples/demo-e2e.ps1` | Happy path (Windows) |
| `examples/demo-e2e-resilience.sh` | LLM-down resilience segment |
| `demo-report.json` | Generated at runtime (**gitignored**) |

```bash
export REPORT=./demo-report.json
export CRITERIA=./examples/demo-criteria.yaml
./examples/demo-e2e.sh
```

## Video script (5 minutes)

| Time | Beat | Show |
|------|------|------|
| 0:00–0:30 | Problem + advisor framework | Slide or pipeline-framework Figure 1 |
| 0:30–2:00 | Happy path | `demo-e2e.sh` or GUI Advisor wizard |
| 2:00–2:45 | **Operational verdict (P0-B)** | `chaosgen verdict --criteria examples/demo-expectation-criteria.yaml` — claim → PASS/FAIL + rationale ([advisor-demo-verdict-beat.md](advisor-demo-verdict-beat.md)) |
| 2:45–3:30 | **Resilience** | Stop Ollama → generate → fallback UNKNOWN → Scenarios: 0 → promote rejected |
| 3:30–4:30 | Promote + catalog | `--from-report` + `--criteria-file` |
| 4:30–5:00 | Evaluate / optional P5 | `chaosgen evaluate --ab`; `incidents --chronic --since 7d` |

### 30-second narrative

> ChaosGen implements the advisor's research framework: filter real incidents via frequency-severity gatekeeper, describe unknowns structurally, promote to known catalog after human approval, then chaos-test and **verify against an operational expectation** (not just before/after charts) with accepted residual risk — closing the loop toward predictive maintenance.

## Screenshot checklist (G6.5)

| File | Capture |
|------|---------|
| `docs/assets/demo-gatekeeper-cli.png` | CLI gatekeeper table after `generate --show-transient` |
| `docs/assets/demo-promote-flow.png` | Before/after `promoted_scenarios.json` or catalog view |
| `docs/assets/demo-fallback-gui.png` | Descriptions fallback badge + Scenarios placeholder |

Advisor hand-drawn scan stays **local** (`docs/assets/advisor-workflow.png` gitignored).

## Anti-patterns (do not use on stage)

| Wrong | Why |
|-------|-----|
| `promote --incident-id 0` without `--from-report` | P4 ClickException |
| `incidents --state described` without `--from-report` | Reads empty `history.db` |
| Main demo = `--from-catalog` only | Skips gatekeeper → describe loop |
| Main demo = `--skip-gatekeeper` | Hides thesis USP |

## TRANSIENT-only telemetry

If gatekeeper shows only TRANSIENT (`node_cpu` blips): valid demo — explain monitor-only path; use resilience beat or export with HTTP 5xx for REAL/CHRONIC.
