# Modular monolith lab (P0)

Compose stack simulating a modular monolith: three logical modules as containers plus Prometheus telemetry bait for ChaosGen evaluation.

## Topology

| Service | Role | Host port |
|---------|------|-----------|
| `monolith-app` | Primary app module (Pumba kill target) | 18081 |
| `module-api` | API module (netem latency target) | 18082 |
| `module-db` | Data module (Redis stand-in) | 16379 |
| `prometheus` | Metrics / blackbox probes | 19090 |

Container logical names match `chaosgen/config/profile_presets.py` (`monolith-app`, `module-api`, `module-db`).

## Bring up

```bash
cd labs/modular-monolith
docker compose up -d
docker compose ps
curl -sf http://127.0.0.1:18081/health
curl -sf http://127.0.0.1:18082/health
```

Windows (PowerShell):

```powershell
cd labs/modular-monolith
docker compose up -d
Invoke-WebRequest http://127.0.0.1:18081/health
```

## ChaosGen

```bash
chaosgen analyze --config examples/modular-monolith-settings.yaml --check
```

Live inject (P0): ensure `inject.dry_run: false` in the example settings and Docker is reachable.

Expectations for post-chaos verdict:

```bash
# After an experiment, evaluate against:
# examples/modular-monolith-expectations.yaml
```

## Tear down

```bash
docker compose down
```
