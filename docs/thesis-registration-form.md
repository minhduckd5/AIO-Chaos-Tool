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

**An AI-assistant for Chaos Engineering**

---

## Thesis goals and objectives

*(brief description of the project output)*

Build **ChaosGen**, an AI assistant that helps operators run Chaos Engineering safely across the full lifecycle: ingest telemetry, detect anomalies, filter real incidents, suggest architecture-aware chaos scenarios, obtain human approval (HITL), execute on Kubernetes/Docker/bare metal, and evaluate KPIs.

**Specific objectives:**

Formalize an *Unknown → Describe → Known* advisory loop for multi-architecture systems; implement a Python pipeline (`chaosgen`) with ML detection, LLM/catalog advisor, HITL safety controls, and multi-tool inject (UCAL); evaluate deeply on microservices then extend to at least one other architecture; deliver a working prototype, documentation, thesis report, and an end-to-end demo (analyze → generate → approve → inject → evaluate).

---

## Requirements

*(list all major requirements for the thesis work)*

Support multiple architecture profiles (microservices primary; modular monolith as second live inject; others via catalog/dry-run); form-first profile selection; metrics/logs ingest with ML on selected public/lab datasets; REAL/CHRONIC gatekeeper and HITL before inject; blast-radius and dry-run safety (lab/staging only); UCAL over common chaos tools; CLI + GUI; and academic deliverables (docs, thesis, lab demo) with measurable KPIs on ≥2 architecture types.

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
