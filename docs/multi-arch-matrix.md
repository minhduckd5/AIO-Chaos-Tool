# Multi-Architecture Profile Matrix

Generated: 2026-09-02T03:02:30.094493+00:00

| Architecture | Tier | Connect | Catalog | Exec env | Inject module | dry_run |
|--------------|------|---------|---------|----------|---------------|---------|
| microservices | P0 | OK | 5 | kubernetes | kubectl-chaos | True |
| modular_monolith | P0 | OK | 2 | docker | pumba | False |
| event_driven | P1 | OK | 2 | docker | broker | True |
| monolith | P1 | OK | 2 | docker | docker | True |
| client_server | P1 | OK | 2 | docker | toxiproxy | True |
| serverless | P1 | OK | 1 | - | - | True |

## Catalog samples

### microservices
- Upstream dependency timeout cascade
- DNS resolution failure for internal service
- Partial network partition between pods
- Replica reduction below minimum threshold
- OOM kill of memory-intensive service

### modular_monolith
- Inter-module API latency under load
- Module container kill during request burst

### event_driven
- Kafka broker unavailability with consumer group
- Message flood beyond consumer processing capacity

### monolith
- Disk fill to 95% under write-heavy load
- CPU starvation under concurrent requests

### client_server
- Server overload under concurrent client connections
- Network latency between client and server tiers

### serverless
- Function cold start timeout under concurrent invocations
