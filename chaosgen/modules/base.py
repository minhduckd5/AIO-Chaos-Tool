"""
Base class for all chaos tool modules.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BaseChaosModule(ABC):
    """Base class for all chaos engineering tool integrations."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the chaos module.
        
        Args:
            config: Configuration dictionary for the chaos tool
        """
        self.config = config or {}
        self.name = self.__class__.__name__
    
    @abstractmethod
    def validate_config(self) -> bool:
        """
        Validate the configuration for this chaos tool.
        
        Returns:
            bool: True if configuration is valid, False otherwise
        """
        pass
    
    @abstractmethod
    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a chaos action.
        
        Args:
            action: The action to execute
            params: Parameters for the action
            
        Returns:
            Dict containing execution results
        """
        pass
    
    @abstractmethod
    def get_available_actions(self) -> List[str]:
        """
        Get list of available actions for this chaos tool.
        
        Returns:
            List of action names
        """
        pass
    
    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """
        Get current status of the chaos tool.
        
        Returns:
            Dict containing status information
        """
        pass
    
    def get_name(self) -> str:
        """Get the name of this chaos module."""
        return self.name
