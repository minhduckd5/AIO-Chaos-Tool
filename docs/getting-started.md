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

## Advisor loop (core thesis workflow)

### 1. Analyze + generate scenarios

```bash
# Live stack
chaosgen analyze
chaosgen generate --save-report ./report.json

# Or analyze + generate in one step
chaosgen analyze --generate --top-n 5
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
3. [IT Project Proposal](IT_PROJECT_PROPOSAL.md) — thesis defense depth
4. [Best Practices](best-practices.md) — lab safety and operator discipline
