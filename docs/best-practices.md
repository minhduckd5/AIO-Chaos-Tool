# Best Practices for Chaos Engineering with AIO Chaos Tool

This guide provides best practices for using AIO Chaos Tool effectively and safely.

## General Principles

### 1. Start Small
- Begin with non-critical environments (dev, test, staging)
- Start with simple, low-impact experiments
- Gradually increase complexity and scope
- Move to production only after thorough testing

### 2. Define Hypotheses
Before running any chaos experiment, define:
- **What you're testing**: "Our system can handle database latency"
- **Expected outcome**: "API response time stays under 2s with 1s DB latency"
- **Success criteria**: Measurable metrics to validate the hypothesis
- **Rollback plan**: How to restore normal operations

### 3. Monitor Everything
- Set up comprehensive monitoring before experiments
- Watch metrics during experiments:
  - Response times
  - Error rates
  - CPU/Memory usage
  - Queue lengths
  - Database connections
- Have alerting in place

### 4. Blast Radius Control
- Limit the scope of experiments
- Use namespaces, labels, or tags to target specific components
- Start with a single instance, then scale gradually
- Set maximum limits on affected resources

## Module-Specific Best Practices

### Chaos Toolkit

**Best Practices**:
- Write declarative experiments in JSON/YAML
- Include rollback steps in every experiment
- Use steady-state hypothesis validation
- Version control your experiment files

**Example Experiment Structure**:
```json
{
  "title": "Database latency tolerance",
  "description": "System handles 1s database latency",
  "steady-state-hypothesis": {
    "title": "Application is healthy",
    "probes": [...]
  },
  "method": [
    {
      "type": "action",
      "name": "inject-latency",
      "provider": {...}
    }
  ],
  "rollbacks": [...]
}
```

### Kube-Monkey

**Best Practices**:
- Use pod labels to target specific deployments
- Start with low kill_value (e.g., 10-20%)
- Set max_kill to limit simultaneous terminations
- Ensure pods have proper readiness/liveness probes
- Test during business hours when support is available

**Safety Tips**:
```yaml
kube-monkey:
  namespace: staging  # Start here
  enabled: true
  max_kill: 1        # Only one at a time
  kill_value: 10     # Only 10% probability
```

### Pumba

**Best Practices**:
- Target non-critical containers first
- Use moderate delays (100-500ms) initially
- Test packet loss at low rates (5-10%) first
- Combine with application monitoring
- Use duration limits for network chaos

**Gradual Approach**:
```bash
# Start with small delay
aio-chaos execute --module pumba --action delay_network \
  --params '{"container": "api", "delay": "50ms"}'

# Gradually increase
aio-chaos execute --module pumba --action delay_network \
  --params '{"container": "api", "delay": "200ms"}'

# Test packet loss
aio-chaos execute --module pumba --action loss_network \
  --params '{"container": "api", "loss": "5"}'
```

### Chaos Monkey

**Best Practices**:
- Ensure auto-scaling is configured
- Test during business hours
- Start with non-critical instances
- Verify monitoring and alerting first
- Have incident response team ready

**Safety Configuration**:
```yaml
chaos-monkey:
  enabled: false  # Disable by default
  schedule_enabled: false
  accounts: ["staging-only"]
  regions: ["us-west-2"]
```

### Toxiproxy

**Best Practices**:
- Create proxies for all critical dependencies
- Start with small latency values (100-500ms)
- Test bandwidth limits gradually
- Document baseline performance metrics
- Combine multiple toxics carefully

**Progressive Testing**:
```bash
# 1. Baseline test
aio-chaos execute --module toxiproxy --action add_latency \
  --params '{"proxy_name": "redis", "latency": 100, "jitter": 10}'

# 2. Moderate latency
aio-chaos execute --module toxiproxy --action add_latency \
  --params '{"proxy_name": "redis", "latency": 500, "jitter": 100}'

# 3. High latency (if system handles moderate well)
aio-chaos execute --module toxiproxy --action add_latency \
  --params '{"proxy_name": "redis", "latency": 2000, "jitter": 500}'
```

### Muxy

**Best Practices**:
- Start with low error injection rates (1-5%)
- Test different HTTP status codes separately
- Monitor client retry behavior
- Test during off-peak hours initially
- Use gradual intensity increases

**Safe Error Injection**:
```bash
# Low rate first
aio-chaos execute --module muxy --action inject_http_error \
  --params '{"status_code": 500, "rate": 0.01}'

# Increase if system handles it well
aio-chaos execute --module muxy --action inject_http_error \
  --params '{"status_code": 500, "rate": 0.05}'
```

## Experiment Workflow

### 1. Preparation Phase
```bash
# Check system health
aio-chaos status

# Verify monitoring is active
# Review baseline metrics
# Brief team on experiment
```

### 2. Execution Phase
```bash
# Run experiment
aio-chaos execute --module [module] --action [action] --params [params]

# Monitor metrics continuously
# Watch for unexpected behavior
# Be ready to abort
```

### 3. Analysis Phase
- Compare metrics to baseline
- Validate hypothesis
- Document findings
- Identify improvements
- Plan next experiments

### 4. Recovery Phase
- Remove chaos conditions
- Verify system recovery
- Check for lingering effects
- Update documentation

## Configuration Best Practices

### Use Version Control
```bash
# Store configs in Git
git add config.yaml
git commit -m "Add chaos config for API service"
```

### Environment-Specific Configs
```
configs/
  ├── dev.yaml
  ├── staging.yaml
  └── production.yaml  # Most conservative settings
```

### Validate Configurations
```bash
# Test config loading
aio-chaos --config config.yaml status

# Verify module settings
aio-chaos --config config.yaml list-modules
```

## Safety Checklist

Before running chaos experiments:

- [ ] Monitoring and alerting configured
- [ ] Team notified of experiment
- [ ] Rollback plan documented
- [ ] Tested in lower environments
- [ ] Blast radius defined and limited
- [ ] Success/failure criteria defined
- [ ] Incident response team available
- [ ] Backup and recovery verified
- [ ] Customer impact assessed
- [ ] Business stakeholders informed

## Common Pitfalls to Avoid

### ❌ Don't
- Run experiments without monitoring
- Test in production first
- Affect all instances simultaneously
- Run experiments without team knowledge
- Forget to document results
- Skip rollback planning
- Ignore warning signs
- Test during critical business periods

### ✅ Do
- Start small and gradual
- Monitor continuously
- Document everything
- Communicate with team
- Plan for rollbacks
- Learn from each experiment
- Share findings
- Automate repeated experiments

## Metrics to Track

### Application Metrics
- Request latency (p50, p95, p99)
- Error rate
- Throughput (requests/second)
- Active connections

### Infrastructure Metrics
- CPU utilization
- Memory usage
- Network I/O
- Disk I/O

### Business Metrics
- User experience scores
- Transaction completion rates
- Revenue impact
- Customer complaints

## Example: Complete Experiment

```bash
# 1. Check baseline
aio-chaos status --module pumba

# 2. Record baseline metrics
# (Use your monitoring system)

# 3. Define hypothesis
# "API service handles 200ms network delay with <5% error rate"

# 4. Run experiment
aio-chaos execute --module pumba --action delay_network \
  --params '{"container": "api-service", "delay": "200ms"}'

# 5. Monitor for 5 minutes
# Watch error rates, latency, throughput

# 6. Analyze results
# Compare to baseline
# Validate hypothesis

# 7. Document findings
# Update runbook
# Plan improvements if needed
```

## Resources

- [Principles of Chaos Engineering](https://principlesofchaos.org/)
- [Chaos Engineering Book](https://www.oreilly.com/library/view/chaos-engineering/9781491988459/)
- [Google SRE Book](https://sre.google/books/)

## Getting Help

- Review documentation: `docs/`
- Check examples: `examples/`
- Open issues: GitHub Issues
- Community discussions: GitHub Discussions

Remember: The goal of chaos engineering is to build confidence in your system's ability to handle turbulent conditions, not to cause outages!
