"""
Main orchestrator for AIO Chaos Tool.
Manages all chaos engineering modules.
"""

from typing import Dict, Any, List, Optional
from .modules.base import BaseChaosModule
from .modules.chaos_toolkit import ChaosToolkitModule
from .modules.kube_monkey import KubeMonkeyModule
from .modules.pumba import PumbaModule
from .modules.chaos_monkey import ChaosMonkeyModule
from .modules.toxiproxy import ToxiproxyModule
from .modules.muxy import MuxyModule
from .config.loader import ConfigLoader


class ChaosOrchestrator:
    """Main orchestrator for managing all chaos tools."""
    
    # Module registry
    MODULE_REGISTRY = {
        'chaos-toolkit': ChaosToolkitModule,
        'kube-monkey': KubeMonkeyModule,
        'pumba': PumbaModule,
        'chaos-monkey': ChaosMonkeyModule,
        'toxiproxy': ToxiproxyModule,
        'muxy': MuxyModule
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the chaos orchestrator.
        
        Args:
            config_path: Path to configuration file
        """
        self.config_loader = ConfigLoader(config_path) if config_path else ConfigLoader()
        self.modules: Dict[str, BaseChaosModule] = {}
        self._initialize_modules()
    
    def _initialize_modules(self) -> None:
        """Initialize all configured chaos modules."""
        module_configs = self.config_loader.get_all_modules()
        
        for module_name, module_class in self.MODULE_REGISTRY.items():
            config = module_configs.get(module_name, {})
            self.modules[module_name] = module_class(config)
    
    def get_module(self, module_name: str) -> Optional[BaseChaosModule]:
        """
        Get a specific chaos module.
        
        Args:
            module_name: Name of the module
            
        Returns:
            BaseChaosModule instance or None
        """
        return self.modules.get(module_name)
    
    def list_modules(self) -> List[str]:
        """
        List all available chaos modules.
        
        Returns:
            List of module names
        """
        return list(self.modules.keys())
    
    def execute_action(self, module_name: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a chaos action on a specific module.
        
        Args:
            module_name: Name of the module
            action: Action to execute
            params: Parameters for the action
            
        Returns:
            Execution result
        """
        module = self.get_module(module_name)
        
        if not module:
            return {
                'success': False,
                'error': f'Module not found: {module_name}',
                'available_modules': self.list_modules()
            }
        
        try:
            return module.execute(action, params)
        except KeyError as e:
            return {
                'success': False,
                'error': f'Invalid parameter: {str(e)}',
                'module': module_name,
                'action': action
            }
        except ValueError as e:
            return {
                'success': False,
                'error': f'Invalid value: {str(e)}',
                'module': module_name,
                'action': action
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Execution failed: {str(e)}',
                'error_type': type(e).__name__,
                'module': module_name,
                'action': action
            }
    
    def get_module_status(self, module_name: str) -> Dict[str, Any]:
        """
        Get status of a specific module.
        
        Args:
            module_name: Name of the module
            
        Returns:
            Module status
        """
        module = self.get_module(module_name)
        
        if not module:
            return {
                'success': False,
                'error': f'Module not found: {module_name}'
            }
        
        return module.get_status()
    
    def get_all_status(self) -> Dict[str, Any]:
        """
        Get status of all modules.
        
        Returns:
            Status of all modules
        """
        return {
            module_name: module.get_status()
            for module_name, module in self.modules.items()
        }
    
    def get_module_actions(self, module_name: str) -> List[str]:
        """
        Get available actions for a specific module.
        
        Args:
            module_name: Name of the module
            
        Returns:
            List of available actions
        """
        module = self.get_module(module_name)
        
        if not module:
            return []
        
        return module.get_available_actions()
    
    def get_all_actions(self) -> Dict[str, List[str]]:
        """
        Get all available actions for all modules.
        
        Returns:
            Dictionary mapping module names to their available actions
        """
        return {
            module_name: module.get_available_actions()
            for module_name, module in self.modules.items()
        }
