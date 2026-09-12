# Architecture Decision Record: PySide6 Desktop GUI Lifecycle Policy

**Status:** Approved & Frozen  
**Date:** September 2026  
**Context:** Phase 2A Transition to Self-Hosted Web UI SPA  
**Primary Interface:** Single-Page Application (React 18 + Vite + Tailwind CSS + TanStack Query) served by FastAPI sidecar at `http://127.0.0.1:8765/`.

---

## 1. Background & Rationale

During Phase 1, ChaosGen provided a desktop GUI built with `PySide6` and `PySide6-Fluent-Widgets`. While effective for local development, enterprise SRE deployments require:
1. Remote multi-tenant browser accessibility without X11/Wayland or remote desktop forwarding.
2. Zero-install client operations for on-call engineers.
3. Strict SOC2/ISO27001 credential masking without local secret cache leaks.
4. Industrial incident-console ergonomics inspired by [VersusControl/versus-incident](https://github.com/VersusControl/versus-incident).

In Phase 2, the primary user interface is transitioning to a self-hosted Single-Page Application (SPA) bundled inside the FastAPI server and served via `StaticFiles` at `/`.

---

## 2. Policy & Invariants

### 2.1 Frozen in Place under `extras_require["gui"]`
- The `chaosgen/gui/` codebase remains preserved in-place to avoid breaking existing Phase 1 workflows or regressing any of the 707 automated unit and integration tests.
- Desktop GUI dependencies (`PySide6>=6.5.0`, `PySide6-Fluent-Widgets>=1.5.0`) remain strictly isolated under `extras_require["gui"]` in `setup.py`.
- Production and containerized deployments only install `chaosgen[api]`, keeping container images slim and headless.

### 2.2 Dual-Inject Operational Limit
- Dual injection (running an injection from the desktop PySide6 GUI and the Web UI simultaneously) is strictly an **operational limit**.
- Mutating operations in the FastAPI layer are protected by an in-process `mutating_lock`, `X-Operator-Name` mandatory header, and pre-execution blast radius verification.

### 2.3 Feature Freeze
- No new features, LLM workflows, or protocol modifications will be authored for `chaosgen/gui/`.
- All future UI enhancements (such as multi-cluster topology visualizers, automated blast radius heatmaps, and Grafana embed panels) will be implemented exclusively in the React Web SPA (`web/`).

### 2.4 Shared Core Logic
- Both the Web UI and legacy desktop GUI consume the exact same underlying core packages:
  - `chaosgen.orchestrator.ChaosOrchestrator`
  - `chaosgen.safety.governance.BlastRadiusController`
  - `chaosgen.storage.audit.AuditStore`
  - `chaosgen.storage.run_resolver.resolve_run_history`
  - `chaosgen.storage.orphan_store.sweep_orphans`
