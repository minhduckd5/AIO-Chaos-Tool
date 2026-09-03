# Gatekeeper audit (Oct upstream)

Date: 2026-09-01  
Scope: `chaosgen/schemas/incidents.py`, `chaosgen/ml/gatekeeper.py`, wiring in `chaosgen/advisor/pipeline.py`  
Glossary: `docs/thesis-glossary.md`  
Tests: `pytest tests/test_gatekeeper.py --no-cov` → **15 passed**

## 1. Enum & schema vs glossary

| Glossary `\texttt{}` | `IncidentVerdict` enum | Code value |
|----------------------|------------------------|------------|
| `NOISE` | `IncidentVerdict.NOISE` | `"noise"` |
| `TRANSIENT` | `IncidentVerdict.TRANSIENT` | `"transient"` |
| `REAL` | `IncidentVerdict.REAL` | `"real"` |
| `CHRONIC` | `IncidentVerdict.CHRONIC` | `"chronic"` |

**Result: PASS.** Exactly four members; no legacy aliases on the enum. Uppercase in thesis / lowercase storage matches glossary rule.

## 2. `passes_downstream` routing

```python
# IncidentCandidate.passes_downstream
return self.verdict in (IncidentVerdict.REAL, IncidentVerdict.CHRONIC)
```

| Verdict | In `filter()` return list? | `passes_downstream` | Describe / UNKNOWN? |
|---------|----------------------------|---------------------|---------------------|
| `NOISE` | **No** (dropped + `logger.info` audit) | N/A | No |
| `TRANSIENT` | Yes (monitor list) | **False** | No — pipeline uses `passed = [c for c in candidates if c.passes_downstream]` before `ScenarioDescriber` |
| `REAL` / `CHRONIC` | Yes | **True** | Yes → describe batch |

**Result: PASS** for advisor short-circuit.

**Note (design, not fail):** TRANSIENT is retained in `AdvisorReport.incident_candidates` for UI/CLI monitor tables (`filtered_transient_count`), but never enters describe/UNKNOWN. NOISE is excluded from candidates and counted in `filtered_noise_count`.

`skip_gatekeeper=True` bypass exists (debug only; logged warning) — out of scope for thesis demo default.

## 3. Threshold & persistence heuristics

Not raw IF score / IQR inside gatekeeper. Inputs are **post-cluster** signals:

| Signal | Source |
|--------|--------|
| Frequency | `sample_count / window_hours` (+ optional `LookbackStateStore` cumulative) |
| Severity | Mapped from `AnomalySeverity` enum → {0.25, 0.50, 0.75, 1.0} |
| Log boost | Metric error-feature tokens **and** severe log keywords (strict AND) |
| Service-error boost | Metric error + concrete service attribution → force `REAL` |

Default thresholds (`GatekeeperSettings`):

- `frequency_low_threshold` = 0.5 /h  
- `frequency_high_threshold` = (see settings; tests use ≥2.0 /h high; CHRONIC when freq > high×2)  
- `severity_low_threshold` = 0.4  
- `severity_high_threshold` = (HIGH/CRITICAL band in matrix tests)

Matrix behavior (validated by unit tests):

- low freq + low sev → `NOISE`  
- mixed gray zone → `TRANSIENT`  
- high freq + high sev → `REAL`  
- very high freq + high sev → `CHRONIC`  
- high freq + log correlation → boost `TRANSIENT`→`REAL`

**Result: PASS** for documented frequency×severity design.  
**Caveat:** Isolation Forest does not feed a continuous anomaly score into the matrix; IF/K-Means produce clusters, then gatekeeper classifies those clusters. That matches thesis “gatekeeper after anomaly detection,” not “IF score thresholds.”

## 4. Unit tests

```text
pytest tests/test_gatekeeper.py --no-cov
15 passed
```

Covers: NOISE/TRANSIENT/REAL/CHRONIC matrix, strict log boost, edge cases, lookback accumulation, service target metadata.

## Verdict

| Criterion | Status |
|-----------|--------|
| 1 Enum ↔ glossary | **PASS** |
| 2 Downstream routing | **PASS** |
| 3 Threshold heuristics | **PASS** (freq×sev + boosts; not IF raw score) |
| 4 Unit tests | **PASS** (15) |

**Ready for advisor audit:** UNKNOWN→DESCRIBED, SafetyPolicy whitelist at generate, fallback, promote-after-PASS.
