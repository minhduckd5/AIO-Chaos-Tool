# Getting Started with ChaosGen

> **Thesis / lab use only** — not a production-ready product. Use staging or local
> clusters you own. See the disclaimer at the top of [README.md](../README.md).

This guide is a **short operator path**. README covers telemetry options in depth;
this file focuses on install, config paths, and the **advisor loop** (P4 commands).

## Prerequisites

- Python **3.10+**
- `pip` and a virtual environment (recommended)
- For live analysis: reachable **Prometheus** and **Loki** (or an offline export bundle)
- For LLM steps: **Ollama** locally (default) or cloud API keys in `.env`

## Installation

```bash
git clone https://github.com/minhduckd5/ChaosGen.git
cd ChaosGen
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/mac: source .venv/bin/activate
pip install -e ".[dev]"
```

Desktop GUI (optional):

```bash
pip install -e ".[gui]"
python -m chaosgen.gui.main
```

## Config paths

ChaosGen uses XDG-style paths (not a repo-local config):

| OS | Config directory |
|----|------------------|
| Linux / macOS | `~/.config/chaosgen/` |
| Windows | `%APPDATA%/chaosgen/` |

| File | Purpose |
|------|---------|
| `settings.yaml` | Observability URLs, gatekeeper thresholds, LLM provider |
| `.env` | API keys (`OPENAI_API_KEY`, etc.) — **never commit** |
| `last_report.json` | Latest advisor report (auto-saved from GUI / `--save-report`) |
| `history.db` | Cross-run analytics (P5; enabled by default) |

Initialize from the registry-vm example:

```bash
chaosgen config init
# or copy examples/registry-vm-settings.yaml into your config dir
```

## Verify connectivity

```bash
chaosgen analyze --check
```

Offline bundle (no live Prometheus/Loki):

```bash
chaosgen analyze --export /path/to/observability-export
```

## Telemetry windows & auto-K (P7)

| Mode | CLI | Notes |
|------|-----|-------|
| Relative | `--hours 48` | Last N hours from now (default from `telemetry.default_lookback_hours`) |
| Absolute | `--start 2026-07-20T08:00:00Z --end 2026-07-22T08:00:00Z` | Live Prom/Loki only; mutually exclusive with `--hours` |
| Export | `--export ./exports/7d-…` | Window from bundle `start_utc`/`end_utc`; drives gatekeeper `lookback_hours` |

`chaosgen generate` accepts the same `--hours` / `--start` / `--end` / `--export` flags (no hardcoded 24h).

**Auto-K:** `anomaly.clustering_mode: auto` picks KMeans `k` via silhouette on the anomalous subset (`min_clusters`…`max_clusters`). Use `fixed` + `n_clusters` for reproducible demos. This is unrelated to `--top-n` (scenario ranker).

## Tuning sensitivity (P8)

Demo knobs (settings.yaml or CLI override; CLI wins for one run):

| Knob | settings path | CLI |
|------|---------------|-----|
| Lookback / window | `telemetry.*` (P7) | `--hours` / `--start`/`--end` |
| Max clusters | `anomaly.max_clusters` | (settings / GUI) |
| Confidence | `advisor.confidence_threshold` | `--confidence-threshold` |
| Top N scenarios | `advisor.top_n_scenarios` | `--top-n` |
| Gatekeeper | `gatekeeper.*` | (settings) |

Lab knobs (usually settings-only): `features.*`, `ingest.log_query`, `ranking.*`, `safety.*`.  
See `examples/registry-vm-settings.yaml` for a full annotated block.

```bash
chaosgen analyze --hours 48 --top-n 10 --confidence-threshold 0.55 --generate
chaosgen generate --config ./examples/registry-vm-settings.yaml --top-n 3
```

## Advisor loop (core thesis workflow)

### 1. Analyze + generate scenarios

```bash
# Live stack
chaosgen analyze --hours 24
chaosgen generate --hours 24 --save-report ./report.json

# Or analyze + generate in one step
chaosgen analyze --hours 48 --generate --top-n 5

# Offline export (recommended for thesis demos)
chaosgen analyze --export /path/to/exports/peak-bundle --generate
```

Gatekeeper may leave **zero scenarios** when all clusters are NOISE/TRANSIENT or
describe fallbacks — that is expected. See [Best Practices](best-practices.md).

Debug only (bypass gatekeeper):

```bash
chaosgen generate --skip-gatekeeper --save-report ./report.json
```

### 2. Inspect incidents

```bash
# From saved report (P4 snapshot)
chaosgen incidents --from-report ./report.json

# From SQLite history (P5 default)
chaosgen incidents
chaosgen incidents --chronic --since 7d
```

### 3. Promote a validated scenario (HITL)

Requires a criteria file and explicit approval:

```bash
# criteria.yaml example:
# http_health: https://your-service/health

chaosgen promote --from-report ./report.json \
  --incident-id 0 --experiment 0 \
  --approved-by "your-name" \
  --criteria-file ./criteria.yaml
```

Promoted entries land in `promoted_scenarios.json` and re-enter the catalog.

### 3b. Operational verdict (P0-B — advisor demo)

Evaluate an SLA-style **claim** (standalone or after chaos):

```bash
chaosgen verdict --criteria ./examples/demo-expectation-criteria.yaml --no-poll
```

Narration script: [advisor-demo-verdict-beat.md](advisor-demo-verdict-beat.md).

### 4. Run approved experiments

```bash
chaosgen run --dry-run    # validate first
chaosgen run              # interactive HITL per scenario
```

### 5. Evaluate

```bash
chaosgen evaluate
chaosgen evaluate --ab
```

## Model Training & Inference (Centroid Stabilization)

By default, `analyze` and `generate` train the `IsolationForest` and `KMeans` models dynamically "on-the-fly" on your lookback window. For consistent and precise anomaly detection (ensuring cluster IDs do not drift between runs), you can split training and inference:

### 1. Train and save model (Dev Mode)

Train the models on a curated baseline telemetry export bundle or live stack, and save the serialized joblib state:

```bash
# Option A: Train on offline export dataset (Recommended)
chaosgen train-model --export /path/to/baseline-export --output-model ./models/baseline_model.joblib

# Option B: Train on live telemetry stack
chaosgen train-model --live --hours 24 --output-model ./models/baseline_model.joblib
```

Training also writes `./models/baseline_model.labels.json` — edit `name` / `description`
per cluster (e.g. `cpu_spike`, `db_lock`) so inference can surface stable labels.

Point `anomaly.default_model_path` in `settings.yaml` at the joblib so analyze/generate
classify without re-fitting when you omit `--model-path`.

### 2. Run Inference using the pre-trained model (Client Mode)

Load the pre-trained centroids for anomaly classification to ensure consistent cluster IDs:

```bash
# Analyze telemetry in inference mode
chaosgen analyze --export /path/to/new-export --model-path ./models/baseline_model.joblib

# Generate chaos experiments using the pre-trained model
chaosgen generate --export /path/to/new-export --model-path ./models/baseline_model.joblib
```

## GUI equivalent

Open the **Advisor** view: run analysis, review **Gatekeeper** / **Descriptions** tabs,
export report, promote via dialog. Scenarios tab shows a message when the pipeline
produces zero experiments after filtering.

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `command not found: chaosgen` | Activate venv; run `pip install -e ".[dev]"` |
| Gatekeeper rows but 0 scenarios | TRANSIENT verdict or describe fallback — not a bug |
| LLM errors | Missing key in `.env` or Ollama not running |
| Empty Prometheus series | Use `--export` bundle or check `settings.yaml` URLs |

## Next steps

1. [E2E Demo Guide](e2e-demo.md) — P6 thesis defense script (happy path + resilience)
2. [Architecture summary](architecture.md) — layers, packages, storage model
2. [Pipeline Framework](pipeline-framework.md) — advisor research model → code mapping
3. [Architecture](architecture.md) — system layers and package map
4. [Best Practices](best-practices.md) — lab safety and operator discipline
