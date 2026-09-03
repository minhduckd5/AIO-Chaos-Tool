# ADR: Chaos Toolkit Experiment as Canonical Runtime

**Status:** Accepted  
**Date:** 2026-08-26  
**Context:** ChaosGen was growing a parallel inject orchestrator (`kubectl-chaos` + custom `ChaosExperiment`) while thesis requires UCAL over Chaos Toolkit and time is limited.

## Decision

1. **Canonical experiment language** = [Chaos Toolkit Experiment Open API](https://chaostoolkit.org/reference/api/experiment/) (`title`, `description`, `method`, optional `steady-state-hypothesis`, `rollbacks`).
2. **Primary executor** = `chaos run` via `ChaosToolkitModule` (real subprocess).
3. **ChaosGen owns** anomaly → advisor/HITL → evaluation/KPI; CTK owns experiment orchestration and multi-action `method`.
4. **Network/stress** = `chaosk8s.chaosmesh.*` (requires Chaos Mesh in-cluster). Pod terminate uses `chaosk8s.pod.actions` (no Mesh required).
5. **`kubectl-chaos`** = optional escape hatch (`inject.executor=kubectl`), not a second schema.

## Consequences

- GUI/builder emit CTK JSON; multi-service = multiple `method` actions.
- Legacy `ChaosExperiment` remains adapter input until advisor emits CTK natively.
- Lab must install `chaostoolkit` + `chaostoolkit-kubernetes` on the operator host; kubeconfig reaches the cluster.
