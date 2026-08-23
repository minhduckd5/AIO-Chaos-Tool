# Advisor Demo Note — Expectation Verdict Beat (P0-B)

> **Audience:** thesis advisor demo (live or recorded), not the written thesis chapters.  
> **Goal:** show that ChaosGen ends with an **operational verdict**, not only “metrics changed.”

## One-sentence claim (say this out loud)

> Under CPU / load stress, the system’s autoscaling (or recovery) claim is tested against an SLA — ChaosGen returns **PASS/FAIL with a human-readable rationale**, like: *“capacity claim exists, but ready replicas did not meet threshold within the SLA window.”*

That is the Edisoft registration-day lesson: design on paper ≠ proven under load.

## Demo beats (add after the existing P6 happy path)

| Time | Beat | What to show |
|------|------|----------------|
| … | (existing) Gatekeeper → Describe → Promote | `demo-e2e` / Advisor GUI |
| +0:00 | **Claim** | Open `examples/demo-expectation-criteria.yaml` — read `claim:` aloud |
| +0:30 | **Pre-check** | `chaosgen verdict --criteria …` (or dry-run) while system is healthy → expect PASS or clear baseline |
| +1:00 | **Chaos + load** | Approve/run CPU starvation / load scenario (catalog tag `autoscaling`) + optional k6 |
| +2:00 | **Verdict** | Post-run: FAIL/PASS + **rationale** printed / saved — not just HTTP 200 |
| +2:30 | **Close** | “Residual risk accepted if PARTIAL; we do not chase 100%.” |

## Narration script (≈45 seconds)

1. “Earlier we filtered real incidents and promoted a known scenario.”  
2. “Chaos engineering value is the **verdict against an expectation**.”  
3. “Claim: ready capacity recovers within N seconds under CPU stress.”  
4. “After injection we query Prometheus — if the threshold is late or missed, verdict is **FAIL** with that reason.”  
5. “That is the gap between ‘we configured autoscaling’ and ‘it actually held under load.’”

## Artifacts to keep on screen

| Artifact | Path |
|----------|------|
| Expectation criteria | `examples/demo-expectation-criteria.yaml` |
| Verdict CLI | `chaosgen verdict --criteria <file> [--prometheus-url …]` |
| **Evaluation GUI (stakeholders)** | Evaluation tab → **Operational outcome** (claim / status / where to improve) |
| Optional model (P0-A) | `models/lab_multi_baseline.joblib` + edited `.labels.json` |
| Peak export (offline story) | `…/exports/7d-20260724-151303 (peak)` |

After `chaosgen verdict` or a chaos run that writes `last_verdict.json`, open the **Evaluation** tab and show the green/amber/red status to non-tech stakeholders.

## Anti-patterns for this beat

| Don’t | Why |
|-------|-----|
| Stop at “before vs after charts” | Advisor already rejected that as the core value |
| Only `http_health: localhost` | Too weak — looks like a ping, not an SLA |
| Skip stating the **claim** | Verdict without a claim is just a boolean |
| Promise 100% resilience | Framework accepts residual risk (`PARTIAL`) |

## Lab prep checklist (before advisor sits down)

- [ ] Prometheus URL reachable from the demo machine  
- [ ] `demo-expectation-criteria.yaml` query matches **your** deployment labels  
- [ ] One chaos scenario that stresses CPU/capacity is runnable (`--dry-run` first)  
- [ ] Optional: k6 or traffic generator ready for the load half of the story  
- [ ] Practice once: healthy → PASS; under stress or wrong threshold → FAIL + readable rationale  

## Relationship to written thesis

Written chapters can describe architecture without this beat. **Defense credibility** needs at least one recorded or live run that ends on the verdict slide/CLI output.
