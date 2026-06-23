# ChaosGen Architecture (Summary)

> **Thesis prototype** — see [Pipeline Framework](pipeline-framework.md) for the advisor
> research model and [IT Project Proposal](IT_PROJECT_PROPOSAL.md) for full technical depth.

ChaosGen is a **pipeline-oriented Python monolith** (`chaosgen` package): CLI + PySide6 GUI
over a shared advisor orchestrator, HITL execution, and six chaos-tool adapters. Hybrid
architecture auto-discovery is **deferred**; the active profile is **microservices**.

## Current scope

| Component | Status |
|-----------|--------|
| Telemetry ingestion (Prometheus + Loki) | **Active** |
| Anomaly detection (IsolationForest + KMeans) | **Active** |
| Incident gatekeeper (`?? real ??`) | **Active** (P1) |
| Unknown → describe → known promote loop | **Active** (P2–P3) |
| Shared `run_advisor_pipeline()` (CLI + GUI) | **Active** (P4) |
| SQLite analytics history (`history.db`) | **Active** (P5) |
| HITL orchestrator + 6 chaos adapters + UCAL | **Active** |
| Hybrid discovery auto-probe | **Deferred** (`DISCOVERY_ENABLED = False`) |

## Layered view

```
┌─────────────────────────────────────────────────────────┐
│  CLI (click)  ·  PySide6 GUI                           │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  run_advisor_pipeline()  —  single orchestrator (P4)     │
│  gatekeeper → describe → filter → interpret → generate   │
└──────────────────────────┬──────────────────────────────┘
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
┌─────────────┐   ┌─────────────┐   ┌──────────────────┐
│ ingestion/  │   │ ml/         │   │ advisor/         │
│ Prometheus  │   │ anomalies   │   │ LLM, catalog,    │
│ + Loki      │   │ + gatekeeper│   │ ranker, promoter │
└─────────────┘   └─────────────┘   └──────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  ScenarioRanker → HITL → UCAL → ChaosOrchestrator        │
│  + safety/ (blast radius, dead man's switch)             │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│  modules/ — Chaos Toolkit, Pumba, Toxiproxy, … (×6)      │
└─────────────────────────────────────────────────────────┘
```

## Advisor data flow (active path)

```
Prometheus / Loki
  → TelemetryCollector → FeatureEngineer → AnomalyDetector (clusters)
  → IncidentGatekeeper (REAL / CHRONIC only downstream)
  → ScenarioDescriber (DESCRIBED; fallbacks blocked at step 4.5)
  → LLMAdvisor → ScenarioGenerator
  → ScenarioRanker → HITL approve
  → UCAL → Orchestrator → chaos modules
  → Evaluation (KPI / A/B)
```

Optional persistence after analysis (P5): `history.db` (analytics) plus
`last_report.json` (CLI/GUI handoff). Runtime catalog SOT: `promoted_scenarios.json`.

## Core packages

| Package | Role |
|---------|------|
| `advisor/pipeline.py` | Shared orchestrator — gatekeeper through scenario gen |
| `ml/gatekeeper.py` | Frequency × severity verdict matrix |
| `advisor/scenario_describer.py` | Structured describe for unknown incidents |
| `advisor/catalog_promoter.py` | HITL promote into known catalog |
| `storage/history.py` | SQLite cross-run analytics (not runtime SOT) |
| `config/` | `settings.yaml`, XDG paths, `.env` secret refs |
| `ingestion/` | Prometheus + Loki clients |
| `orchestrator.py` | State machine + HITL gate |
| `modules/` + `ucal/` | Six chaos tools behind one abstraction |
| `discovery/` | Retained code; not on the active runtime path |

## Storage model (golden rule)

| Artifact | Role |
|----------|------|
| `settings.yaml` | Operator hints (URLs, gatekeeper thresholds); secret **refs** only |
| `.env` in config dir | API keys and tokens (never in git) |
| `last_report.json` | Latest advisor snapshot for `incidents` / `promote` |
| `promoted_scenarios.json` | HITL-approved known catalog (P3) |
| `history.db` | Append-only analytics — chronic patterns, ranker recency |

## Re-enabling discovery (future)

1. Set `DISCOVERY_ENABLED = True` in `chaosgen/config/scope.py`
2. Restore Discovery in the GUI (wired to the same flag)
3. Extend catalog and advisor prompts per architecture type

## Design principles

1. **One orchestrator** — CLI, GUI, and tests call `run_advisor_pipeline()`
2. **Human-in-the-loop** — no autonomous production chaos execution
3. **Scope discipline** — stabilize Unknown→Known on microservices before multi-arch
4. **JSON runtime SOT, SQLite analytics** — avoid dual-writer confusion (P5)

## Further reading

- [Pipeline Framework](pipeline-framework.md) — Figures 1–2, module mapping, implementation status
- [Getting Started](getting-started.md) — install and first advisor-loop run
- [Best Practices](best-practices.md) — thesis-lab safety and operator notes
