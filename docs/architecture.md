# AIO Chaos Tool Architecture

## Overview

AIO Chaos Tool follows a modular architecture that allows easy integration of different chaos engineering tools through a unified interface.

## Component Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    CLI Interface                         │
│                    (cli.py)                             │
└───────────────────┬─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│              Chaos Orchestrator                          │
│              (orchestrator.py)                           │
│  • Module Management                                     │
│  • Action Routing                                        │
│  • Configuration Loading                                 │
└───────────────────┬─────────────────────────────────────┘
                    │
       ┌────────────┴────────────┐
       │                         │
       ▼                         ▼
┌──────────────┐        ┌────────────────┐
│   Config     │        │    Modules     │
│   Loader     │        │   Registry     │
└──────────────┘        └────────┬───────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
         ┌────────────────────┐    ┌────────────────────┐
         │  Chaos Module 1    │    │  Chaos Module N    │
         │  (base.py impl)    │    │  (base.py impl)    │
         └────────────────────┘    └────────────────────┘
```

## Core Components

### 1. CLI Interface (`cli.py`)

The command-line interface provides user interaction through various commands:

- `list-modules` - List available chaos modules
- `list-actions` - List available actions
- `status` - Show module status
- `execute` - Execute chaos actions

### 2. Chaos Orchestrator (`orchestrator.py`)

The orchestrator is the central component that:

- Manages all chaos modules
- Routes actions to appropriate modules
- Handles configuration loading
- Coordinates execution across modules

Key methods:
- `execute_action()` - Execute an action on a module
- `get_module_status()` - Get module status
- `list_modules()` - List all modules
- `get_module_actions()` - Get available actions

### 3. Configuration Loader (`config/loader.py`)

Handles configuration file loading and management:

- Supports YAML and JSON formats
- Provides module-specific configuration
- Global configuration settings

### 4. Base Chaos Module (`modules/base.py`)

Abstract base class that defines the interface all chaos modules must implement:

```python
class BaseChaosModule(ABC):
    @abstractmethod
    def validate_config(self) -> bool
    
    @abstractmethod
    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]
    
    @abstractmethod
    def get_available_actions(self) -> List[str]
    
    @abstractmethod
    def get_status(self) -> Dict[str, Any]
```

### 5. Chaos Module Implementations

Each chaos tool has its own module implementation:

- `chaos_toolkit.py` - Chaos Toolkit integration
- `kube_monkey.py` - Kube-Monkey integration
- `pumba.py` - Pumba integration
- `chaos_monkey.py` - Chaos Monkey integration
- `toxiproxy.py` - Toxiproxy integration
- `muxy.py` - Muxy integration

## Data Flow

### Action Execution Flow

```
User Command
    ↓
CLI Parser
    ↓
Orchestrator.execute_action()
    ↓
Module Selection
    ↓
Module.execute(action, params)
    ↓
Action Implementation
    ↓
Return Result
    ↓
Format & Display
```

### Configuration Flow

```
Config File (YAML/JSON)
    ↓
ConfigLoader.load_config()
    ↓
Parse & Validate
    ↓
Store in memory
    ↓
Orchestrator initialization
    ↓
Module initialization with config
```

## Module Registration

Modules are registered in the orchestrator's `MODULE_REGISTRY`:

```python
MODULE_REGISTRY = {
    'chaos-toolkit': ChaosToolkitModule,
    'kube-monkey': KubeMonkeyModule,
    'pumba': PumbaModule,
    'chaos-monkey': ChaosMonkeyModule,
    'toxiproxy': ToxiproxyModule,
    'muxy': MuxyModule
}
```

## Adding New Modules

To add a new chaos tool:

1. Create a new module class inheriting from `BaseChaosModule`
2. Implement all abstract methods
3. Register the module in `orchestrator.MODULE_REGISTRY`
4. Add configuration schema to documentation

Example:

```python
# modules/new_tool.py
from .base import BaseChaosModule

class NewToolModule(BaseChaosModule):
    def validate_config(self) -> bool:
        # Implementation
        pass
    
    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        # Implementation
        pass
    
    def get_available_actions(self) -> List[str]:
        # Implementation
        pass
    
    def get_status(self) -> Dict[str, Any]:
        # Implementation
        pass
```

## Design Principles

### 1. Modularity
Each chaos tool is encapsulated in its own module with minimal dependencies.

### 2. Extensibility
Easy to add new chaos tools by implementing the base interface.

### 3. Consistency
Unified interface across all chaos tools for consistent user experience.

### 4. Configuration-Driven
All tools can be configured through a single configuration file.

### 5. Separation of Concerns
Clear separation between CLI, orchestration, and implementation layers.

## Future Enhancements

Potential areas for expansion:

1. **Real Tool Integration**: Connect to actual chaos tool APIs
2. **Experiment Scheduling**: Schedule chaos experiments
3. **Result Tracking**: Store and analyze experiment results
4. **Web UI**: Web-based interface for easier interaction
5. **Kubernetes CRDs**: Define experiments as Kubernetes custom resources
6. **Plugin System**: Dynamic module loading
7. **Metrics Integration**: Connect with Prometheus, Grafana
8. **Rollback Automation**: Automatic rollback on failures
