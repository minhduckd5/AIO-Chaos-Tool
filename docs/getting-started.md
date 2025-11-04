# Getting Started with AIO Chaos Tool

## Prerequisites

- Python 3.8 or higher
- pip package manager
- Basic understanding of chaos engineering principles

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/minhduckd5/AIO-Chaos-Tool.git
cd AIO-Chaos-Tool
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Install the Package

```bash
pip install -e .
```

## First Steps

### Verify Installation

```bash
aio-chaos --version
```

### List Available Modules

```bash
aio-chaos list-modules
```

You should see output like:
```
Available Chaos Modules:
----------------------------------------
  • chaos-toolkit
  • kube-monkey
  • pumba
  • chaos-monkey
  • toxiproxy
  • muxy
```

### Check Available Actions

```bash
aio-chaos list-actions
```

### Check Module Status

```bash
aio-chaos status
```

## Creating Your First Configuration

Create a file called `my-config.yaml`:

```yaml
global:
  log_level: info

modules:
  pumba:
    target_containers:
      - "my-container"
    interval: "10s"
  
  toxiproxy:
    host: localhost
    port: 8474
```

Use your configuration:

```bash
aio-chaos --config my-config.yaml status
```

## Running Your First Chaos Experiment

### Example 1: Container Chaos with Pumba

```bash
# Simulate killing a container
aio-chaos execute \
  --module pumba \
  --action kill_container \
  --params '{"container": "my-app", "signal": "SIGKILL"}'
```

### Example 2: Network Chaos with Toxiproxy

```bash
# Simulate network latency
aio-chaos execute \
  --module toxiproxy \
  --action add_latency \
  --params '{"proxy_name": "redis", "latency": 1000}'
```

### Example 3: Kubernetes Chaos with Kube-Monkey

```bash
# Simulate pod termination
aio-chaos execute \
  --module kube-monkey \
  --action terminate_pods \
  --params '{"namespace": "default", "count": 1}'
```

## Next Steps

1. Read the [Architecture Guide](architecture.md) to understand how the tool works
2. Explore [Example Configurations](../examples/) for more complex scenarios
3. Learn about individual chaos tools in the [Module Documentation](modules.md)
4. Review [Best Practices](best-practices.md) for chaos engineering

## Troubleshooting

### Command Not Found

If you get "command not found" error, ensure the package is installed:

```bash
pip install -e .
```

### Module Not Found

If you get module import errors, ensure all dependencies are installed:

```bash
pip install -r requirements.txt
```

### Configuration Errors

Validate your YAML configuration syntax:

```bash
python -c "import yaml; yaml.safe_load(open('config.yaml'))"
```
