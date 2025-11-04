# Module Documentation

This document provides detailed information about each chaos engineering module integrated into AIO Chaos Tool.

## Chaos Toolkit

**Purpose**: Declarative chaos engineering platform for defining and running chaos experiments.

### Configuration

```yaml
chaos-toolkit:
  experiment_path: "./experiments/experiment.json"
  rollback_enabled: true
```

### Available Actions

#### run_experiment
Run a chaos experiment from a JSON/YAML file.

**Parameters**:
- `experiment_file` (string): Path to experiment file

**Example**:
```bash
aio-chaos execute --module chaos-toolkit --action run_experiment \
  --params '{"experiment_file": "./experiment.json"}'
```

#### validate
Validate an experiment file format.

**Parameters**:
- `experiment_file` (string): Path to experiment file

**Example**:
```bash
aio-chaos execute --module chaos-toolkit --action validate \
  --params '{"experiment_file": "./experiment.json"}'
```

#### discover
Discover available chaos toolkit extensions and actions.

**Parameters**: None

**Example**:
```bash
aio-chaos execute --module chaos-toolkit --action discover
```

---

## Kube-Monkey

**Purpose**: Kubernetes chaos testing tool that randomly terminates pods.

### Configuration

```yaml
kube-monkey:
  namespace: default
  enabled: true
  max_kill: 1
  kill_value: 50
```

### Available Actions

#### terminate_pods
Randomly terminate pods in a namespace.

**Parameters**:
- `namespace` (string): Kubernetes namespace
- `count` (integer): Number of pods to terminate

**Example**:
```bash
aio-chaos execute --module kube-monkey --action terminate_pods \
  --params '{"namespace": "production", "count": 2}'
```

#### schedule_termination
Schedule pod terminations.

**Parameters**:
- `namespace` (string): Kubernetes namespace
- `schedule` (string): Schedule pattern (e.g., "random", "daily")

**Example**:
```bash
aio-chaos execute --module kube-monkey --action schedule_termination \
  --params '{"namespace": "staging", "schedule": "random"}'
```

#### get_victims
List potential victim pods that could be terminated.

**Parameters**:
- `namespace` (string): Kubernetes namespace

**Example**:
```bash
aio-chaos execute --module kube-monkey --action get_victims \
  --params '{"namespace": "default"}'
```

---

## Pumba

**Purpose**: Docker chaos testing tool for container and network failures.

### Configuration

```yaml
pumba:
  target_containers:
    - "my-app"
    - "my-service"
  interval: "10s"
```

### Available Actions

#### kill_container
Kill a Docker container.

**Parameters**:
- `container` (string): Container name or ID
- `signal` (string): Kill signal (default: SIGKILL)

**Example**:
```bash
aio-chaos execute --module pumba --action kill_container \
  --params '{"container": "my-app", "signal": "SIGTERM"}'
```

#### pause_container
Pause a running container.

**Parameters**:
- `container` (string): Container name or ID
- `duration` (string): Pause duration (e.g., "30s")

**Example**:
```bash
aio-chaos execute --module pumba --action pause_container \
  --params '{"container": "my-app", "duration": "30s"}'
```

#### stop_container
Stop a running container.

**Parameters**:
- `container` (string): Container name or ID

**Example**:
```bash
aio-chaos execute --module pumba --action stop_container \
  --params '{"container": "my-app"}'
```

#### delay_network
Add network delay to container.

**Parameters**:
- `container` (string): Container name or ID
- `delay` (string): Delay amount (e.g., "100ms")

**Example**:
```bash
aio-chaos execute --module pumba --action delay_network \
  --params '{"container": "my-app", "delay": "100ms"}'
```

#### loss_network
Add network packet loss to container.

**Parameters**:
- `container` (string): Container name or ID
- `loss` (string): Packet loss percentage

**Example**:
```bash
aio-chaos execute --module pumba --action loss_network \
  --params '{"container": "my-app", "loss": "10"}'
```

#### rate_limit
Limit network rate for container.

**Parameters**:
- `container` (string): Container name or ID
- `rate` (string): Rate limit (e.g., "1000kbit")

**Example**:
```bash
aio-chaos execute --module pumba --action rate_limit \
  --params '{"container": "my-app", "rate": "1000kbit"}'
```

---

## Chaos Monkey

**Purpose**: Netflix's AWS chaos testing tool for terminating EC2 instances.

### Configuration

```yaml
chaos-monkey:
  enabled: true
  schedule_enabled: true
  accounts:
    - "prod-account"
  regions:
    - "us-east-1"
```

### Available Actions

#### terminate_instance
Terminate an EC2 instance.

**Parameters**:
- `instance_id` (string): EC2 instance ID
- `region` (string): AWS region

**Example**:
```bash
aio-chaos execute --module chaos-monkey --action terminate_instance \
  --params '{"instance_id": "i-1234567890", "region": "us-east-1"}'
```

#### schedule_termination
Schedule instance terminations.

**Parameters**:
- `schedule` (string): Schedule pattern
- `accounts` (array): AWS account IDs

**Example**:
```bash
aio-chaos execute --module chaos-monkey --action schedule_termination \
  --params '{"schedule": "daily", "accounts": ["123456789"]}'
```

#### enable
Enable Chaos Monkey.

**Parameters**: None

**Example**:
```bash
aio-chaos execute --module chaos-monkey --action enable
```

#### disable
Disable Chaos Monkey.

**Parameters**: None

**Example**:
```bash
aio-chaos execute --module chaos-monkey --action disable
```

---

## Toxiproxy

**Purpose**: Network chaos and toxicity simulator for testing resilience.

### Configuration

```yaml
toxiproxy:
  host: localhost
  port: 8474
  proxies:
    - name: "redis"
      listen: "0.0.0.0:16379"
      upstream: "localhost:6379"
```

### Available Actions

#### add_latency
Add network latency to a proxy.

**Parameters**:
- `proxy_name` (string): Proxy name
- `latency` (integer): Latency in milliseconds
- `jitter` (integer): Jitter in milliseconds

**Example**:
```bash
aio-chaos execute --module toxiproxy --action add_latency \
  --params '{"proxy_name": "redis", "latency": 1000, "jitter": 100}'
```

#### add_bandwidth_limit
Limit bandwidth for a proxy.

**Parameters**:
- `proxy_name` (string): Proxy name
- `rate` (integer): Rate in KB/s

**Example**:
```bash
aio-chaos execute --module toxiproxy --action add_bandwidth_limit \
  --params '{"proxy_name": "redis", "rate": 1000}'
```

#### add_slow_close
Slow down connection closing.

**Parameters**:
- `proxy_name` (string): Proxy name
- `delay` (integer): Delay in milliseconds

**Example**:
```bash
aio-chaos execute --module toxiproxy --action add_slow_close \
  --params '{"proxy_name": "redis", "delay": 1000}'
```

#### add_timeout
Add timeouts to connections.

**Parameters**:
- `proxy_name` (string): Proxy name
- `timeout` (integer): Timeout in milliseconds

**Example**:
```bash
aio-chaos execute --module toxiproxy --action add_timeout \
  --params '{"proxy_name": "redis", "timeout": 1000}'
```

#### add_slicer
Slice data into smaller chunks.

**Parameters**:
- `proxy_name` (string): Proxy name
- `average_size` (integer): Average chunk size in bytes
- `delay` (integer): Delay between chunks in microseconds

**Example**:
```bash
aio-chaos execute --module toxiproxy --action add_slicer \
  --params '{"proxy_name": "redis", "average_size": 64, "delay": 10}'
```

#### create_proxy
Create a new proxy.

**Parameters**:
- `name` (string): Proxy name
- `listen` (string): Listen address
- `upstream` (string): Upstream address

**Example**:
```bash
aio-chaos execute --module toxiproxy --action create_proxy \
  --params '{"name": "mysql", "listen": "0.0.0.0:13306", "upstream": "localhost:3306"}'
```

#### delete_proxy
Delete a proxy.

**Parameters**:
- `name` (string): Proxy name

**Example**:
```bash
aio-chaos execute --module toxiproxy --action delete_proxy \
  --params '{"name": "mysql"}'
```

---

## Muxy

**Purpose**: Proxy for simulating real-world distributed system failures.

### Configuration

```yaml
muxy:
  proxy_host: localhost
  proxy_port: 8181
  target_host: localhost
  target_port: 8080
```

### Available Actions

#### inject_latency
Inject network latency.

**Parameters**:
- `latency` (integer): Latency in milliseconds
- `jitter` (integer): Jitter in milliseconds

**Example**:
```bash
aio-chaos execute --module muxy --action inject_latency \
  --params '{"latency": 1000, "jitter": 100}'
```

#### inject_http_error
Inject HTTP error responses.

**Parameters**:
- `status_code` (integer): HTTP status code
- `rate` (float): Error rate (0.0-1.0)

**Example**:
```bash
aio-chaos execute --module muxy --action inject_http_error \
  --params '{"status_code": 500, "rate": 0.1}'
```

#### inject_tcp_reset
Inject TCP connection resets.

**Parameters**:
- `rate` (float): Reset rate (0.0-1.0)

**Example**:
```bash
aio-chaos execute --module muxy --action inject_tcp_reset \
  --params '{"rate": 0.1}'
```

#### inject_throttle
Throttle bandwidth.

**Parameters**:
- `bytes_per_second` (integer): Bandwidth limit in bytes/second

**Example**:
```bash
aio-chaos execute --module muxy --action inject_throttle \
  --params '{"bytes_per_second": 10000}'
```

#### inject_disruption
Inject random disruptions.

**Parameters**:
- `disruption_type` (string): Type of disruption
- `intensity` (float): Disruption intensity (0.0-1.0)

**Example**:
```bash
aio-chaos execute --module muxy --action inject_disruption \
  --params '{"disruption_type": "mixed", "intensity": 0.5}'
```

---

## Common Patterns

### Checking Module Status

```bash
# Check all modules
aio-chaos status

# Check specific module
aio-chaos status --module pumba
```

### Listing Actions

```bash
# List all actions for all modules
aio-chaos list-actions

# List actions for specific module
aio-chaos list-actions --module toxiproxy
```

### Using Configuration Files

```bash
# Use a config file for all commands
aio-chaos --config my-config.yaml status
aio-chaos --config my-config.yaml execute --module pumba --action kill_container
```

---

## Notes

- All modules in this version provide simulation mode
- To use real chaos injection, install the actual chaos tools
- Always test in non-production environments first
- Review and understand each action before executing
