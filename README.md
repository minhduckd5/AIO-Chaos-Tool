# ChaosGen

[![License](https://img.shields.io/github/license/minhduckd5/ChaosGen)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**AI-Driven Chaos Scenario Generator** — Automatically discovers your system architecture, detects the existing observability stack, and uses an LLM pipeline (local Ollama or cloud providers) to generate, rank, and execute targeted chaos experiments with a Human-in-the-Loop approval gate.

## Overview

ChaosGen replaces manual chaos scenario authoring with an AI pipeline that:

1. **Discovers** your environment (Kubernetes, Docker Compose, Bare Metal, Cloud VM)
2. **Classifies** your architecture (Microservices, Monolith, Event-Driven, etc.)
3. **Checks** for Prometheus/Grafana/Loki and bootstraps them if missing
4. **Generates** context-aware chaos scenarios via LLM (Ollama, OpenAI, Anthropic, Groq)
5. **Ranks** scenarios by confidence, historical value, coverage gap, and safety margin
6. **Runs** approved experiments through a state-machine orchestrator with automatic rollback

## Integrated Chaos Tools

ChaosGen wraps six chaos engineering tools behind a unified adapter interface:

- **Chaos Toolkit** — declarative JSON/YAML experiment runner
- **Kube-Monkey** — random pod terminator for Kubernetes
- **Pumba** — Docker container chaos (kill, pause, network delay/loss)
- **Chaos Monkey** — Netflix's EC2 instance terminator
- **Toxiproxy** — network chaos proxy (latency, bandwidth, timeout)
- **Muxy** — HTTP/TCP fault injector

## Installation

```bash
git clone https://github.com/minhduckd5/ChaosGen.git
cd ChaosGen
pip install -e ".[dev]"
```

For the desktop GUI:

```bash
pip install -e ".[gui]"
```

## Quick Start

### 1. Discover Your Environment

```bash
chaosgen discover
```

Probes for Kubernetes, Docker, and cloud metadata. Classifies architecture and checks observability tools.

### 2. Bootstrap Observability (if missing)

```bash
# Auto-detect tier and install
chaosgen bootstrap

# Force a specific tier
chaosgen bootstrap --tier k8s      # Helm install kube-prometheus-stack
chaosgen bootstrap --tier docker   # Inject Prometheus/Grafana into docker-compose.yml
chaosgen bootstrap --tier script   # Generate install_prometheus.sh for bare metal
```

### 3. Generate AI Chaos Scenarios

```bash
# Use local Ollama (default — air-gapped, no API cost)
chaosgen generate --provider ollama --top-n 5

# Use OpenAI GPT-4o
chaosgen generate --provider openai --top-n 5

# Pull from the pre-built scenario catalog (no LLM required)
chaosgen generate --from-catalog --arch microservices

# Override detected architecture
chaosgen generate --provider anthropic --arch event_driven --top-n 3
```

### 4. Run Approved Experiments (HITL Gate)

```bash
# Review and approve/reject each scenario interactively
chaosgen run

# Dry run — validate without executing
chaosgen run --dry-run
```

### 5. Evaluate Results

```bash
# KPI report
chaosgen evaluate

# A/B comparison: AI-generated vs human-designed
chaosgen evaluate --ab

# Export
chaosgen evaluate --export csv
```

## Configuration

### API Keys (for cloud LLM providers)

Store keys in `~/.chaosgen/.env` (auto-created on first save from the Settings tab):

```env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GROQ_API_KEY=gsk_...
OLLAMA_URL=http://localhost:11434
```

The file is enforced to `chmod 600` on Linux/macOS. Ollama requires no key and is the default provider.

### Experiment Configuration

```yaml
# config.yaml
global:
  log_level: info
  dry_run: false
  safety:
    max_blast_radius_pods_pct: 20
    blocked_namespaces:
      - kube-system
      - monitoring

advisor:
  llm_provider: ollama          # ollama | openai | anthropic | groq
  llm_model: llama3.2:3b
  confidence_threshold: 0.6
  top_n_scenarios: 5

modules:
  pumba:
    target_containers: ["my-app"]
  toxiproxy:
    host: localhost
    port: 8474
```

## Architecture

```
chaosgen/
├── cli.py                    # Click command groups
├── orchestrator.py           # State-machine engine + HITL gate
├── discovery/                # Environment + architecture detection
│   ├── environment_probe.py
│   ├── architecture_classifier.py
│   ├── service_mapper.py
│   └── observability_probe.py
├── bootstrap/                # Observability auto-install
│   ├── exceptions.py
│   ├── observability_installer.py
│   └── connection_verifier.py
├── advisor/                  # AI scenario generation pipeline
│   ├── context_builder.py
│   ├── llm_advisor.py        # Multi-provider (Ollama/OpenAI/Anthropic/Groq)
│   ├── scenario_catalog.py   # Pre-built scenarios by architecture type
│   ├── scenario_generator.py
│   ├── scenario_ranker.py
│   └── manifest_writer.py
├── ml/                       # Anomaly detection (IsolationForest + KMeans)
├── ingestion/                # Prometheus + Loki telemetry clients
├── modules/                  # Chaos tool adapters
├── schemas/                  # Pydantic data models
│   └── discovery.py          # EnvironmentProfile, ServiceMap, etc.
├── safety/                   # Blast radius + dead man's switch
├── evaluation/               # KPI tracker + A/B comparator
├── config/
│   └── secrets.py            # .env-based API key management
├── ucal/                     # Universal Chaos Abstraction Layer
└── gui/                      # PySide6 desktop application
```

## Docker

```bash
# Build
docker build -t chaosgen:dev .

# Run tests
docker run --rm -t chaosgen:dev pytest -q

# Development mount
docker compose --profile dev run --rm chaosgen_dev pytest -q
```

## License

MIT License — see [LICENSE](LICENSE) for details.
