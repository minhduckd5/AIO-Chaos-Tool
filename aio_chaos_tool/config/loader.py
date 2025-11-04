"""
Configuration management for AIO Chaos Tool.
"""

import yaml
import json
from typing import Dict, Any, Optional
from pathlib import Path


class ConfigLoader:
    """Load and manage configuration for chaos tools."""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration loader.
        
        Args:
            config_path: Path to configuration file (YAML or JSON)
        """
        self.config_path = config_path
        self.config = {}
        
        if config_path:
            self.load_config(config_path)
    
    def load_config(self, config_path: str) -> Dict[str, Any]:
        """
        Load configuration from file.
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            Configuration dictionary
        """
        path = Path(config_path)
        
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        with open(config_path, 'r') as f:
            if config_path.endswith(('.yaml', '.yml')):
                self.config = yaml.safe_load(f) or {}
            elif config_path.endswith('.json'):
                self.config = json.load(f)
            else:
                raise ValueError(f"Unsupported configuration format: {config_path}")
        
        return self.config
    
    def get_module_config(self, module_name: str) -> Dict[str, Any]:
        """
        Get configuration for a specific module.
        
        Args:
            module_name: Name of the chaos module
            
        Returns:
            Module configuration dictionary
        """
        return self.config.get('modules', {}).get(module_name, {})
    
    def get_global_config(self) -> Dict[str, Any]:
        """
        Get global configuration.
        
        Returns:
            Global configuration dictionary
        """
        return self.config.get('global', {})
    
    def get_all_modules(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all module configurations.
        
        Returns:
            Dictionary of all module configurations
        """
        return self.config.get('modules', {})
    
    def save_config(self, config_path: Optional[str] = None) -> None:
        """
        Save configuration to file.
        
        Args:
            config_path: Path to save configuration (defaults to loaded path)
        """
        save_path = config_path or self.config_path
        
        if not save_path:
            raise ValueError("No configuration path specified")
        
        with open(save_path, 'w') as f:
            if save_path.endswith(('.yaml', '.yml')):
                yaml.dump(self.config, f, default_flow_style=False)
            elif save_path.endswith('.json'):
                json.dump(self.config, f, indent=2)
