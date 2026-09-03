# ChaosGen lab stacks

Docker Compose and local stacks for multi-architecture thesis demos.

| Profile | Tier | Path | Example settings |
|---------|------|------|------------------|
| modular_monolith | **P0 Live** | [`modular-monolith/`](modular-monolith/) | [`examples/modular-monolith-settings.yaml`](../examples/modular-monolith-settings.yaml) |
| microservices | **P0 Live** | existing K3s boutique (see `registry-vm-settings.yaml`) | [`examples/registry-vm-settings.yaml`](../examples/registry-vm-settings.yaml) |

## Quick start (modular monolith)

```bash
cd labs/modular-monolith
docker compose up -d
curl http://127.0.0.1:18081/health
```

Then point ChaosGen at the example settings:

```bash
chaosgen analyze --config examples/modular-monolith-settings.yaml --check
```
