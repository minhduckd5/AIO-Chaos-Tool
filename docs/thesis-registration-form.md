# Thesis Registration Form — draft content (ChaosGen)

> Source template: *Thesis Registration Form* (IU – VNU-HCM).  
> Copy the fields below into the Word form. Fill `[...]` by hand before submission.  
> **Language: English** (official form). Vietnamese replica: [`thesis-registration-form.vi.md`](thesis-registration-form.vi.md).

---

## Student / supervisor

| Field | Content |
|--------|---------|
| **Student's name** | `[Full name]` |
| **ID** | `[Student ID]` |
| **Email** | `[email@student.hcmiu.edu.vn]` |
| **Phone** | `[Phone number]` |
| **Major** | `[Exact major as registered, e.g. Computer Science / Information Technology]` |
| **Name of Supervisor 1** | Tran Thanh Tung |
| **Name of Supervisor 2** | *(optional — leave blank if none)* |

---

## Thesis title

**ChaosGen: AI-Driven Chaos Engineering for Multi-Architecture Systems**

---

## Thesis goals and objectives

*(brief description of the project output)*

Build **ChaosGen**, an AI-driven Chaos Engineering **control-plane platform** (CLI + GUI) that covers the full lifecycle: telemetry ingestion → anomaly detection → real-incident filtering → architecture-aware chaos scenario generation and ranking → human-in-the-loop (HITL) approval → safe execution on Kubernetes/Docker/bare metal → KPI evaluation.

**Specific objectives:**

1. **Research:** Formalize an *Unknown → Describe → Known* framework with a frequency × severity gatekeeper, shifting from reactive incident handling toward *predictive maintenance* under controlled residual risk. The framework targets **multi-architecture systems** (microservices, monolith, event-driven, serverless/client–server, etc.).
2. **Implementation:** Deliver a Python pipeline (`chaosgen`) with Prometheus/Loki ingestion, ML anomaly detection (IsolationForest/KMeans), LLM/catalog advisor per architecture profile, HITL orchestrator, safety controls (blast radius, dead man’s switch), UCAL wrapping ≥6 chaos tools, and **architecture discovery/profile** selection for catalogs and prompts.
3. **Evaluation (phased):** Stabilize and deeply evaluate on **microservices** (lab/K8s cluster)—at least one concrete *make-sense* case study plus multi-case KPI aggregation; then **extend** to at least one additional architecture (e.g. monolith or event-driven/serverless) to demonstrate multi-architecture coverage.
4. **Deliverables:** Multi-profile prototype, architecture/pipeline documentation, thesis report, and an end-to-end demo (analyze → generate → approve → inject → evaluate) on ≥2 architecture types (microservices as the primary path; a second type based on available lab/dataset).

---

## Requirements

*(list all major requirements for the thesis work)*

1. **Multi-architecture scope:** Design the system for **multiple software architecture styles** (microservices, monolith, event-driven, serverless, etc.). **Phase 1:** stabilize the Unknown→Known loop plus inject/eval on microservices (K8s/Docker). **Phase 2:** enable/complete architecture profiles, catalogs, and prompts for other styles—do not lock the thesis to microservices only.
2. **Discovery / profile:** **Form-first** architecture profile selection (`hints.architecture` + `connect` block in settings/GUI; heuristic auto-discovery deferred). Operator declares intent; observability endpoints are probed when configured. Microservices is the primary evaluated path; **modular monolith** is the second live-inject case (Docker Compose / Pumba). Remaining profiles (event-driven, monolith, client–server, serverless) use catalog + dry-run for thesis coverage.
3. **Data & ML:** Ingest metrics/logs; train/evaluate anomaly models on **selected slices** of public datasets (RCAEval, Nezha, Eadro, etc.) plus lab inject data—do not train on full multi-terabyte dumps; prefer corpora that match each architecture when expanding.
4. **Advisor pipeline:** REAL/CHRONIC gatekeeper; structured describe for unknown incidents; architecture-aware scenario generation/ranking; promote into a known catalog after HITL.
5. **Safe chaos execution:** State machine with mandatory HITL; blast-radius limits; blocked namespaces (e.g. `kube-system`); dry-run support; real inject only on staging/lab (not production).
6. **Tool integration:** UCAL abstraction over Chaos Toolkit, Pumba, Toxiproxy, Kube-Monkey, etc. (and/or Chaos Mesh/Litmus on the university cluster when available).
7. **Evaluation:** ≥1 detailed microservices case study that *makes sense*, plus N-case KPI aggregation (latency, error rate, recovery/rollback); plus ≥1 case/demo for a non-microservices architecture (per available lab/dataset).
8. **UX & operations:** CLI automation + PySide6 GUI for HITL review; secrets via `.env` (no hard-coded credentials); architecture profile selectable/overridable from CLI/GUI.
9. **Academic handover:** Multi-profile SRS/architecture docs, pipeline framework aligned with the advisor’s model, thesis report, and demo on a lab K8s cluster (master + nodes) if access is granted.

---

## Suggested timeline *(if the advisor form/plan asks for weekly detail)*

| Phase | Focus |
|-------|--------|
| Weeks 1–3 | Lock multi-architecture topic, SRS, schemas, architecture profiles |
| Weeks 4–6 | Discovery/bootstrap observability; lab ingest (microservices first) |
| Weeks 7–9 | Advisor + ML + Unknown→Known catalog (microservices profile) |
| Weeks 10–11 | Orchestrator, HITL, UCAL, safety |
| Weeks 12–13 | GUI, KPI/A-B evaluation; complete microservices case study |
| Weeks 14–16 | Form-first multi-arch profiles (P0: MS + modular monolith live; P1: dry-run matrix); thesis + defense |

> **Repo status (Sep 2026):** Microservices flagship loop complete. **Phase 2 delivered:** form-first profile presets, connect validation, modular monolith lab (`labs/modular-monolith/`), Pumba live inject, Prometheus expectation verdict (documented true-negative FAIL + HITL PASS). Matrix rehearsal: `python scripts/demo_multi_arch_matrix.py`. ADR: `docs/adr-multi-arch-form-profiles.md`.

---

## Signatures (on the Word form)

| Party | Fill-in |
|-------|---------|
| Supervisor 1 | Tran Thanh Tung — Date of Signed: `[date]` |
| Student 1 | `[Full name]` — Date of Signed: `[date]` |

---

## Before submission

- Complete name / student ID / email / phone.
- Confirm **Major** wording with the department registry.
- Sign and return the form to the Undergraduate Academic Assistant as instructed on the template.
