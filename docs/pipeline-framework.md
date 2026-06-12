# ChaosGen Pipeline Framework

> **Scope (current):** Microservices-first. Hybrid discovery is temporarily disabled;
> other architecture types will be added incrementally.

This document maps the **advisor research framework** to the **ChaosGen implementation**.

---

## Figure 1 — Research Framework (Advisor)

Conceptual model: anomaly ingestion → incident validation → knowledge refinement → chaos → verify → iterate.

```mermaid
flowchart TD
    subgraph INGEST["1. Data Ingestion"]
        LOGS[Logs / Metrics]
        ABN[abnormal]
        GATE["?? real ??<br/>frequency × severity"]
        PM[predictive maintenance]
        LOGS --> ABN --> GATE --> PM
    end

    subgraph CLASSIFY["2. Known / Unknown"]
        DESC[describe]
        UNK(("In the unknown"))
        KNOWN[known]
        ABN --> DESC --> UNK
        UNK -->|Yes → test chaos| CHAOS
        UNK -->|No → describe| DESC
        UNK --> known
    end

    subgraph CHAOS_PIPE["3. Chaos Engineering"]
        CHAOS[chaos]
        INJ[Introduce errors]
        ROB[check robustness]
        BS(("Buggy Scenarios"))
        CHAOS --> INJ --> ROB --> BS
        known --> BS
    end

    subgraph VERIFY_LOOP["4. Verify & Residual Risk"]
        VER(("Verify"))
        DS[design solutions]
        HANDLE[handle]
        Q100{"100%?"}
        CHAOS --> VER --> DS
        BS --> HANDLE --> Q100
        Q100 -->|No| DS
        DS -.-> BS
    end

    FREQ[frequency / severity] -.-> GATE
```

---

## Figure 2 — ChaosGen Implementation (Microservices Focus)

Same logic, bound to current modules. Dashed boxes are **planned**.

```mermaid
flowchart TD
    subgraph SOURCES["Telemetry"]
        PROM[Prometheus]
        LOKI[Loki]
    end

    subgraph INGEST["Ingestion & ML"]
        COL[TelemetryCollector]
        FE[FeatureEngineer]
        ISO[IsolationForest]
        KM[KMeans]
        GK{{"Gatekeeper<br/>?? real ??<br/>(planned)"}}
        PROM --> COL
        LOKI --> COL
        COL --> FE --> ISO --> KM --> GK
    end

    subgraph CONTEXT["Microservices Context"]
        SCOPE["scope.py<br/>Focused Profile"]
        CTX[ContextBuilder]
        CAT[ScenarioCatalog — known]
        SCOPE --> CTX
    end

    subgraph UNKNOWN["Unknown → Known (planned)"]
        LLM[LLMAdvisor — describe]
        PROMOTE[Promote to Catalog]
        GK --> LLM
        LLM --> PROMOTE --> CAT
    end

    subgraph ADVISOR["Scenario Pipeline"]
        GEN[ScenarioGenerator]
        RANK[ScenarioRanker]
        GK --> CTX
        CTX --> GEN
        CAT --> RANK
        GEN --> RANK
    end

    subgraph EXEC["Execution"]
        HITL{{"HITL Gate"}}
        UCAL[UCAL Translator]
        ORCH[ChaosOrchestrator]
        SAFETY[BlastRadius + DMS]
        RANK --> HITL
        HITL --> UCAL --> ORCH
        ORCH --> SAFETY
    end

    subgraph EVAL["Evaluation"]
        KPI[KPITracker]
        AB[ABComparator]
        ORCH --> KPI --> AB
    end

    style GK fill:#fff2cc,stroke:#d6b656,stroke-dasharray: 5 5
    style PROMOTE fill:#fff2cc,stroke:#d6b656,stroke-dasharray: 5 5
    style SCOPE fill:#d5e8d4,stroke:#82b366
```

---

## Scope Matrix

| Architecture | Discovery | Catalog | LLM Advisor | Status |
|--------------|-----------|---------|-------------|--------|
| **Microservices** | Static profile | Yes | Yes | **Active** |
| Monolith | — | Partial | Partial | Planned |
| Event-driven | — | Partial | Partial | Planned |
| Client-server | — | — | Partial | Planned |
| Serverless | — | — | Partial | Planned |

Re-enable full discovery: set `DISCOVERY_ENABLED = True` in `chaosgen/config/scope.py`.

---

## Module Mapping

| Advisor concept | ChaosGen module | Status |
|-----------------|-----------------|--------|
| Logs → abnormal | `ingestion/` + `ml/anomaly_detector.py` | Implemented |
| frequency × severity | `config/scope.py` + gatekeeper | **Planned** |
| ?? real ?? | Gatekeeper layer | **Planned** |
| In the unknown → describe | `advisor/llm_advisor.py` | Partial |
| known | `advisor/scenario_catalog.py` | Implemented |
| chaos → Introduce errors | `orchestrator.py` + `modules/` | Implemented |
| Verify | `ucal/validation.py` | Partial |
| 100%? → No loop | HITL + `safety/governance.py` | Implemented |
| predictive maintenance | `evaluation/` + history DB | Partial |
