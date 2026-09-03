# ChaosGen Thesis Glossary

Single source of truth for nomenclature across the LaTeX thesis (Chapters 1--6) and the `chaosgen` reference implementation. Authority order: Chapter 3 Section 3.2, then `chaosgen/schemas/`, then this file.

## Locked enums

### Gatekeeper (`IncidentVerdict`)

| Thesis `\texttt{}` | Code enum value | Prose rule |
|-------------------|-----------------|------------|
| `NOISE` | `noise` | Discard; do not enter chaos path |
| `TRANSIENT` | `transient` | Monitor only; no auto-generate inject |
| `REAL` | `real` | Proceed to describe / scenario generation |
| `CHRONIC` | `chronic` | High recurrence; predictive-maintenance candidate; still requires describe/HITL |

Schema: `chaosgen/schemas/incidents.py` (`IncidentVerdict`).

Only `REAL` and `CHRONIC` pass downstream (`passes_downstream`).

### Knowledge lifecycle (`ScenarioKnowledgeState`)

| Thesis `\texttt{}` | Code enum value | Prose rule |
|-------------------|-----------------|------------|
| `UNKNOWN` | `unknown` | Gated incident without reusable playbook |
| `DESCRIBED` | `described` | Structured description exists; weak fallbacks must not promote |
| `KNOWN` | `known` | HITL-approved catalog entry after verify |

Extended runtime tags (`CHAOS_TESTED`, `VERIFIED`) may appear in implementation metadata; the scientific loop emphasized in the thesis is `UNKNOWN` → `DESCRIBED` → `KNOWN`.

Schema: `chaosgen/schemas/scenarios.py` (`ScenarioKnowledgeState`).

**Prose vs code:** Use lowercase in running text for concepts (*unknown incident*, *known catalog*). Use `\texttt{UNKNOWN}` etc. when referring to the state machine symbol.

### Expectation Verdict (`ExperimentVerdict`)

| Thesis `\texttt{}` | Code enum value | Meaning |
|-------------------|-----------------|--------|
| `PASS` | `pass` | Expectation criteria / steady-state bounds satisfied |
| `FAIL` | `fail` | Hard criteria violated |
| `PARTIAL` | `partial` | Residual risk recorded; advisor 100%? → No case |

Schema: `chaosgen/schemas/scenarios.py` (`ExperimentVerdict`).

## Named components

| Term | Usage |
|------|--------|
| **ChaosGen** | Product / thesis system name; AI-driven Chaos Engineering control plane |
| **UCAL** | Unified Chaos Abstraction Layer; execution and portability layer (secondary contribution) |
| **Expectation Verdict** | Post-inject evaluation engine; first mention may include *engine* |
| **gatekeeper** | Lowercase in prose unless starting a sentence |
| **HITL** | Spell out *human-in-the-loop (HITL)* on first use per major chapter |
| **CTK** | Chaos Toolkit; spell out on first use in body chapters |
| **IF** | Isolation Forest when context is ML detection |

## Hyphenation and typography

| Pattern | Rule |
|---------|------|
| control plane | Noun: no hyphen (*ChaosGen control plane*) |
| control-plane | Adjective only if needed (*control-plane architecture*) |
| blast radius | Noun: no hyphen |
| blast-radius | Adjective (*blast-radius limits*) |
| human-in-the-loop | Hyphenated compound; abbreviate to HITL after expansion |
| IF--verdict | LaTeX en-dash for compound modifier; keep in technical prose |
| em-dash `---` | Avoid rhetorical em-dashes in thesis prose; use comma, period, or parentheses |
| en-dash `--` | Ranges and technical compounds (e.g., Online Boutique--style) |

## Laboratory naming (boutique disambiguation)

**Problem:** *boutique* alone reads like marketing copy or is confused with the Google demo name.

| Context | Preferred wording |
|---------|-------------------|
| First mention per chapter | *microservices laboratory deployment (Google Online Boutique--style topology on registry-vm)* |
| Subsequent mentions | *microservices lab*, *registry-vm lab*, or *boutique microservices lab* |
| Avoid | *boutique* standing alone without lab/topology context |

**Evidence runs (N=2):**

| Run ID | Verdict | Role |
|--------|---------|------|
| `flagship-multi-kill` | `PASS` | Resilient recovery; IF false alarm |
| `fail-redis-cart` | `FAIL` | Critical-path harm; IF + verdict aligned |

**Config:** `examples/registry-vm-settings.yaml`  
**Alignment report:** `docs/anomaly-verdict-alignment-combined.md`

**Metrics (evaluable runs = 2):** alignment rate 50%; FAIL detection 100%; PASS specificity 0%.

## Evaluation framing

| Term | Definition |
|------|------------|
| proof of concept (PoC) | Spell out on first use per major section; then PoC |
| qualitative failure-mode analysis | Primary evaluation label; not inferential statistics |
| pipeline integrity | End-to-end analyze → generate → approve → inject → evaluate |
| N=2 | Two evaluable live CTK-backed runs; not two architectures |

**Out of scope claims:** 45% incident reduction, 168% ROI, equal-depth multi-architecture E2E proof.

## Contribution hierarchy (Option B)

| Layer | Role | Defense evidence |
|-------|------|------------------|
| **Primary (scientific)** | Gated control plane + Expectation Verdict: anomaly detection is not harm validation | N=2: PASS specificity 0%, FAIL detection 100% |
| **Organizing (architecture)** | Unknown → Describe → Known (`ScenarioKnowledgeState`) | Registration form + schemas; state model, not catalog completeness KPI |
| **Execution (technical)** | UCAL + HITL + blast radius + dead man's switch | Blocks unsafe inject (`kube-system`, etc.) |
| **Design (expansion)** | Multi-architecture | Title + future work; discovery disabled in thesis eval profile |

October acceptance criteria: [`docs/oct-gated-control-plane-acceptance.md`](oct-gated-control-plane-acceptance.md).

## Seven-stage control plane

1. Telemetry  
2. Anomaly detection (IF + K-Means)  
3. Gatekeeper filter  
4. Knowledge / describe (`UNKNOWN` → `DESCRIBED`)  
5. Scenario generation and ranking  
6. HITL + UCAL inject  
7. Expectation Verdict and optional promote to `KNOWN`

## American English (thesis orthography)

| Use | Avoid |
|-----|-------|
| organizations | organisations |
| summarizes | summarises |
| behavior (if needed in prose) | behaviour |

## Knowledge loop (display)

**Organizing model (not primary claim):** Unknown → Describe → Known  
**Primary claim (Option B):** Gated control plane + Expectation Verdict (anomaly ≠ harm validation)  
**LaTeX:** `\textbf{Unknown $\rightarrow$ Describe $\rightarrow$ Known}` for state lifecycle; emphasize Expectation Verdict / gated inject in Abstract and Conclusion at December polish.
