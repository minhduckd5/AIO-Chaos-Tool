# AIO Chaos Tool Architecture

## Overview

ChaosGen is a **microservices-focused** AI-driven chaos engineering control plane.
Hybrid environment discovery is temporarily scoped off; the pipeline uses a static
microservices profile while core ingestion → anomaly → scenario → HITL → execution
logic is refined.

See **[Pipeline Framework](pipeline-framework.md)** for the advisor research model
and module mapping.

## Current Scope

| Component | Status |
|-----------|--------|
| Telemetry ingestion (Prometheus + Loki) | Active |
| Anomaly detection (IsolationForest + KMeans) | Active |
| Microservices context (`config/scope.py`) | Active |
| Scenario catalog + LLM advisor | Active |
| HITL orchestrator + 6 chaos adapters | Active |
| Hybrid discovery auto-probe | **Disabled** (`DISCOVERY_ENABLED = False`) |
| Gatekeeper `?? real ??` | Planned |
| Unknown → Known promotion loop | Planned |

## Component Architecture

```
┌─────────────────────────────────────────────────────────┐
│              CLI / PySide6 GUI                           │
└───────────────────┬─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│  resolve_discovery_report()  →  microservices profile    │
│  (scope.py when discovery off)                           │
└───────────────────┬─────────────────────────────────────┘
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
┌──────────┐  ┌──────────┐  ┌──────────────┐
│ Ingestion│  │ ML Pipe  │  │ Advisor      │
│ Collector│  │ Anomaly  │  │ Catalog+LLM  │
└────┬─────┘  └────┬─────┘  └──────┬───────┘
     │             │                │
     └─────────────┴────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│  ScenarioRanker → HITL → UCAL → ChaosOrchestrator        │
│  + BlastRadiusController + DeadMansSwitch                │
└───────────────────┬─────────────────────────────────────┘
                    ▼
┌─────────────────────────────────────────────────────────┐
│  Chaos Modules (Toolkit, Pumba, Toxiproxy, Kube-Monkey…)   │
└─────────────────────────────────────────────────────────┘
```

## Core Packages

| Package | Role |
|---------|------|
| `config/scope.py` | Scope guard — microservices focus, discovery toggle |
| `ingestion/` | Prometheus + Loki clients, telemetry collector |
| `ml/` | Feature engineering, IsolationForest anomaly detection |
| `advisor/` | Context builder, LLM advisor, catalog, ranker, generator |
| `orchestrator.py` | State machine + HITL approval gate |
| `modules/` | Chaos tool adapters (6 tools) |
| `safety/` | Blast radius governance, dead man's switch |
| `evaluation/` | KPI tracker, A/B comparator |
| `discovery/` | Hybrid probes (retained, not active in current scope) |

## Data Flow (Microservices Focus)

```
Prometheus/Loki
    → TelemetryCollector
    → FeatureEngineer
    → AnomalyDetector
    → [Gatekeeper — planned]
    → ContextBuilder (microservices profile)
    → LLMAdvisor / ScenarioCatalog
    → ScenarioRanker
    → HITL
    → UCAL → Orchestrator → Chaos Modules
    → Evaluation (KPI / A/B)
```

## Re-enabling Discovery

When multi-architecture support is ready:

1. Set `DISCOVERY_ENABLED = True` in `chaosgen/config/scope.py`
2. Restore Discovery nav item in GUI (automatic when flag is true)
3. Extend catalog and advisor prompts per architecture type

## Design Principles

1. **Modularity** — Each chaos tool behind `BaseChaosModule`
2. **Human-in-the-loop** — No autonomous production chaos execution
3. **Scope discipline** — Ship microservices pipeline before expanding architecture coverage
4. **Off-the-shelf ML** — sklearn + LLM providers, no custom RAG/ML from scratch
