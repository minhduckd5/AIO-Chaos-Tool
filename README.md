# AIO Chaos Tool

[![License](https://img.shields.io/github/license/minhduckd5/AIO-Chaos-Tool)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

**All-In-One Chaos Engineering Tool** - A unified interface for multiple chaos engineering tools, combining the best features of various chaos testing platforms into a single, easy-to-use solution.

## 🎯 Overview

AIO Chaos Tool integrates multiple popular chaos engineering tools into a single framework, allowing you to perform comprehensive chaos testing without switching between different platforms and tools.

## 🛠️ Integrated Chaos Tools

This project combines the following chaos engineering tools:

### 1. **Chaos Toolkit**
A declarative chaos engineering platform that allows you to define and run chaos experiments using JSON/YAML files.

### 2. **Kube-Monkey**
A Kubernetes-native chaos testing tool that randomly terminates pods to test resilience.

### 3. **Pumba**
A Docker chaos testing tool that can kill, stop, pause containers, and inject network problems.

### 4. **Chaos Monkey**
Netflix's original chaos engineering tool for AWS, randomly terminating EC2 instances.

### 5. **Toxiproxy**
A network chaos simulator that can inject latency, bandwidth limitations, and other network conditions.

### 6. **Muxy**
A proxy tool for simulating real-world distributed system failures including network issues and HTTP errors.

## 📦 Installation

### From Source

```bash
git clone https://github.com/minhduckd5/AIO-Chaos-Tool.git
cd AIO-Chaos-Tool
pip install -e .
```

### Requirements

- Python 3.8 or higher
- PyYAML 6.0 or higher

## 🚀 Quick Start

### 1. List Available Modules

```bash
aio-chaos list-modules
```

### 2. View Available Actions

```bash
# List all actions
aio-chaos list-actions

# List actions for a specific module
aio-chaos list-actions --module pumba
```

### 3. Check Module Status

```bash
# Check all modules
aio-chaos status

# Check specific module
aio-chaos status --module chaos-toolkit
```

### 4. Execute Chaos Actions

```bash
# Pumba - Kill a container
aio-chaos execute --module pumba --action kill_container --params '{"container": "my-app", "signal": "SIGKILL"}'

# Toxiproxy - Add latency
aio-chaos execute --module toxiproxy --action add_latency --params '{"proxy_name": "redis", "latency": 1000, "jitter": 100}'

# Kube-Monkey - Terminate pods
aio-chaos execute --module kube-monkey --action terminate_pods --params '{"namespace": "production", "count": 2}'

# Muxy - Inject HTTP errors
aio-chaos execute --module muxy --action inject_http_error --params '{"status_code": 500, "rate": 0.1}'
```

## ⚙️ Configuration

Create a configuration file (YAML or JSON) to customize module settings:

```yaml
# config.yaml
global:
  log_level: info
  dry_run: false

modules:
  chaos-toolkit:
    experiment_path: "./experiments/experiment.json"
    rollback_enabled: true
  
  kube-monkey:
    namespace: default
    enabled: true
    max_kill: 1
    kill_value: 50
  
  pumba:
    target_containers:
      - "my-app"
      - "my-service"
    interval: "10s"
  
  toxiproxy:
    host: localhost
    port: 8474
    proxies:
      - name: "redis"
        listen: "0.0.0.0:16379"
        upstream: "localhost:6379"
```

Use the configuration file:

```bash
aio-chaos --config config.yaml status
```

## 📖 Usage Examples

### Example 1: Network Chaos with Toxiproxy

```bash
# Create a proxy
aio-chaos execute --module toxiproxy --action create_proxy \
  --params '{"name": "redis", "listen": "0.0.0.0:16379", "upstream": "localhost:6379"}'

# Add latency
aio-chaos execute --module toxiproxy --action add_latency \
  --params '{"proxy_name": "redis", "latency": 1000, "jitter": 100}'

# Add bandwidth limit
aio-chaos execute --module toxiproxy --action add_bandwidth_limit \
  --params '{"proxy_name": "redis", "rate": 1000}'
```

### Example 2: Container Chaos with Pumba

```bash
# Kill a container
aio-chaos execute --module pumba --action kill_container \
  --params '{"container": "my-app"}'

# Pause a container
aio-chaos execute --module pumba --action pause_container \
  --params '{"container": "my-app", "duration": "30s"}'

# Add network delay
aio-chaos execute --module pumba --action delay_network \
  --params '{"container": "my-app", "delay": "100ms"}'
```

### Example 3: Kubernetes Chaos with Kube-Monkey

```bash
# Terminate pods
aio-chaos execute --module kube-monkey --action terminate_pods \
  --params '{"namespace": "production", "count": 2}'

# Schedule termination
aio-chaos execute --module kube-monkey --action schedule_termination \
  --params '{"namespace": "staging", "schedule": "random"}'
```

## 🏗️ Architecture

```
aio_chaos_tool/
├── __init__.py          # Package initialization
├── cli.py               # Command-line interface
├── orchestrator.py      # Main orchestrator
├── modules/             # Chaos tool integrations
│   ├── base.py          # Base module class
│   ├── chaos_toolkit.py
│   ├── kube_monkey.py
│   ├── pumba.py
│   ├── chaos_monkey.py
│   ├── toxiproxy.py
│   └── muxy.py
├── config/              # Configuration management
│   └── loader.py
└── utils/               # Utility functions
```

## 🎯 Available Actions by Module

### Chaos Toolkit
- `run_experiment` - Run a chaos experiment
- `validate` - Validate an experiment file
- `discover` - Discover available actions

### Kube-Monkey
- `terminate_pods` - Randomly terminate pods
- `schedule_termination` - Schedule pod terminations
- `get_victims` - Get list of potential victim pods

### Pumba
- `kill_container` - Kill a Docker container
- `pause_container` - Pause a Docker container
- `stop_container` - Stop a Docker container
- `delay_network` - Add network delay
- `loss_network` - Add network packet loss
- `rate_limit` - Limit network rate

### Chaos Monkey
- `terminate_instance` - Terminate an EC2 instance
- `schedule_termination` - Schedule instance terminations
- `enable` - Enable Chaos Monkey
- `disable` - Disable Chaos Monkey

### Toxiproxy
- `add_latency` - Add latency to network calls
- `add_bandwidth_limit` - Limit bandwidth
- `add_slow_close` - Slow down connection closing
- `add_timeout` - Add timeouts
- `add_slicer` - Slice data into smaller chunks
- `create_proxy` - Create a new proxy
- `delete_proxy` - Delete a proxy

### Muxy
- `inject_latency` - Inject network latency
- `inject_http_error` - Inject HTTP error responses
- `inject_tcp_reset` - Inject TCP connection reset
- `inject_throttle` - Throttle bandwidth
- `inject_disruption` - Inject random disruptions

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⚠️ Important Notes

**This is a simulation framework.** The current implementation provides a unified interface to various chaos engineering tools, but does not include the actual chaos tool executables. To use real chaos injection:

1. Install the individual chaos tools you want to use (chaostoolkit, pumba, etc.)
2. Configure the tools according to their documentation
3. Use AIO Chaos Tool as a unified control interface

## 🔗 Related Projects

- [Chaos Toolkit](https://chaostoolkit.org/)
- [Kube-Monkey](https://github.com/asobti/kube-monkey)
- [Pumba](https://github.com/alexei-led/pumba)
- [Chaos Monkey](https://github.com/Netflix/chaosmonkey)
- [Toxiproxy](https://github.com/Shopify/toxiproxy)
- [Muxy](https://github.com/mefellows/muxy)

## 📞 Support

For issues, questions, or contributions, please visit the [GitHub repository](https://github.com/minhduckd5/AIO-Chaos-Tool).
