# ChaosGen Operator Notes (Thesis Lab)

> Generic chaos-engineering theory is widely documented elsewhere. This page covers
> **ChaosGen-specific** safety and interpretation for thesis evaluation runs.

## Scope and environment

- Run only on **clusters or VMs you control** (staging, kind, lab Proxmox/K8s).
- Treat the README thesis disclaimer as binding: **no production deployments**.
- Prefer `--dry-run` on `chaosgen run` until manifests and blast radius look correct.

## Human-in-the-loop (non-negotiable)

- ChaosGen **never** auto-executes generated scenarios in production mode.
- Every experiment path goes through **HITL approval** (`chaosgen run` or GUI).
- **Promote** requires `--approved-by` and a **criteria file** — do not bypass for thesis demos.

## Gatekeeper expectations

The gatekeeper classifies each anomaly cluster before chaos generation:

| Verdict | Meaning | Chaos generation |
|---------|---------|------------------|
| **NOISE** | Low signal | Dropped (counted only) |
| **TRANSIENT** | Monitor | **No** describe / scenarios |
| **REAL** | Actionable | Describe → maybe scenarios |
| **CHRONIC** | Recurring | Describe → scenarios; tracked in `history.db` |

**Common confusion:** Gatekeeper tab shows rows but **Scenarios: 0**. If the sole row is
**TRANSIENT**, or describe produced a **fallback**, zero scenarios is **correct behavior**.

Use `--show-transient` on `chaosgen generate` to include monitor-only rows in CLI output.

## Describe fallbacks

When the LLM describer fails, incidents get `describe_fallback=True`. Step 4.5 **blocks**
them from chaos generation by design. Do not promote fallback descriptions.

## Secrets and config

- Store API keys in config-dir `.env` only (`chaosgen config set-key` or GUI Settings).
- In `settings.yaml`, use `token_ref` / `password_ref` **key names**, not raw secrets.
- Do not commit `history.db`, `last_report.json`, exported `report.json`, or `.env`.

## Storage discipline (P3 / P4 / P5)

| Store | Role |
|-------|------|
| `promoted_scenarios.json` | Runtime known catalog (SOT) |
| `last_report.json` | CLI/GUI handoff snapshot |
| `history.db` | Analytics only — chronic patterns, ranker recency |

If JSON and DB disagree, **trust JSON** for runtime; DB is for history queries.

## Telemetry quality

- Sparse or infra-only metrics (e.g. `node_cpu` with no error logs) often yield NOISE/TRANSIENT.
- For meaningful advisor demos, use exports with **HTTP 5xx**, latency, or correlated logs.
- Longer lookback (`--hours`) helps chronic detection but increases noise volume.
- Gatekeeper **frequency** = samples / `lookback_hours`. Always derive lookback from the
  actual collection window (export metadata or `--start`/`--end`), not an unrelated CLI default.
- Cap `anomaly.max_clusters` in noisy labs; use `clustering_mode: fixed` for reproducible demos.
- Shorter `features.resample_step_seconds` → more feature rows (noisier); larger
  `rolling_window_seconds` → smoother spikes. Raise `anomaly.contamination` only if
  IsolationForest under-flags; document the value in thesis tables.
- Keep `ranking` weights summing to ~1.0 (P8 auto-normalizes with a warning if off).

## Evaluation hygiene

- Run `chaosgen evaluate` after experiments to capture KPI deltas.
- Use `--ab` only when you have a controlled baseline scenario for comparison.
- Document residual risk explicitly in thesis text when verification does not reach 100%.

## When things look “broken”

1. Check **Descriptions** tab — empty means gatekeeper did not pass REAL/CHRONIC downstream.
2. Check verdict column — TRANSIENT ≠ bug.
3. Try `chaosgen incidents --from-report` vs `chaosgen incidents` to see snapshot vs DB.
4. Use `--skip-gatekeeper` **only** for pipeline debugging, never as a demo default.

## Further reading

- [Getting Started](getting-started.md) — command walkthrough
- [Pipeline Framework](pipeline-framework.md) — why the loop is shaped this way
- [Architecture](architecture.md) — components and data flow
