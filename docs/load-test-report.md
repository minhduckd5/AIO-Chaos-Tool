# Load Test Report — Network Latency Scenario (P6 / G6.4)

**Scenario:** `upstream-timeout-cascade` from built-in microservices catalog  
**Fault:** 2000ms downstream latency (Toxiproxy / network chaos path via UCAL)  
**Environment:** Staging / kind cluster or local Toxiproxy — adjust targets to your lab.

## Objective

Validate one network-latency chaos scenario end-to-end with acceptable discovery and rollback latency (targets from IT proposal §6):

| Metric | Target | Result |
|--------|--------|--------|
| Scenario discovery / orchestrator prep (p95) | ≤ 45s | _fill after run_ |
| Rollback / steady-state recovery | ≤ 10s | _fill after run_ |

## Procedure

### 1. Select catalog experiment

```bash
chaosgen generate --from-catalog --arch microservices
# Confirm entry: upstream-timeout-cascade
```

### 2. Dry-run validation

```bash
chaosgen run --dry-run
```

### 3. Execute (staging namespace only)

```bash
# Example — approve interactively on lab cluster
chaosgen run
```

Or manual Toxiproxy latency on downstream dependency per catalog `NetworkFaultSpec`.

### 4. Observe

- Prometheus: `http_request_duration_seconds` p95 or service SLO dashboard
- KPI tracker: `chaosgen evaluate` before/after windows

### 5. Optional Locust load (dev extra)

```bash
pip install -e ".[dev]"
# locust -f tests/load/locustfile.py --host=http://<service-url>
```

## Results template

| Run | Date | Cluster | p95 prep (s) | Rollback (s) | Pass? |
|-----|------|---------|--------------|--------------|-------|
| 1 | YYYY-MM-DD | kind/staging | | | |

## Notes for thesis defense

- Document **HITL approval** before any live inject.
- If live inject is not possible in the defense room, **dry-run + evaluate on historical KPI windows** satisfies G6.4 with committee approval — note limitation in oral defense.
- Catalog reference: `chaosgen/advisor/scenario_catalog.py` → `upstream-timeout-cascade`.

## Sign-off

- [ ] At least one network latency scenario exercised (live or dry-run documented)
- [ ] Metrics recorded in table above
- [ ] G6.4 acceptance criteria documented in demo notes
