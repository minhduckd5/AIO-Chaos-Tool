"""
Chaos Monkey integration module.
Provides integration with Chaos Monkey for AWS chaos testing.
"""

from typing import Dict, Any, List
from .base import BaseChaosModule


class ChaosMonkeyModule(BaseChaosModule):
    """Integration for Chaos Monkey - Netflix's AWS chaos testing tool."""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.enabled = self.config.get('enabled', True)
        self.schedule_enabled = self.config.get('schedule_enabled', True)
        self.accounts = self.config.get('accounts', [])
        self.regions = self.config.get('regions', [])
    
    def validate_config(self) -> bool:
        """Validate Chaos Monkey configuration."""
        return True  # Basic config is optional
    
    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute Chaos Monkey action.
        
        Available actions:
        - terminate_instance: Terminate an EC2 instance
        - schedule_termination: Schedule instance terminations
        - enable: Enable Chaos Monkey
        - disable: Disable Chaos Monkey
        """
        if action == 'terminate_instance':
            return self._terminate_instance(params)
        elif action == 'schedule_termination':
            return self._schedule_termination(params)
        elif action == 'enable':
            return self._enable(params)
        elif action == 'disable':
            return self._disable(params)
        else:
            return {'success': False, 'error': f'Unknown action: {action}'}
    
    def _terminate_instance(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Terminate an EC2 instance."""
        instance_id = params.get('instance_id', 'i-xxxxxxxx')
        region = params.get('region', 'us-east-1')
        
        return {
            'success': True,
            'module': 'chaos-monkey',
            'action': 'terminate_instance',
            'instance_id': instance_id,
            'region': region,
            'message': f'Would terminate instance {instance_id} in region {region}',
            'note': 'This is a simulation. Deploy Chaos Monkey with proper AWS credentials for real chaos.'
        }
    
    def _schedule_termination(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Schedule instance termination."""
        schedule = params.get('schedule', 'daily')
        accounts = params.get('accounts', self.accounts)
        
        return {
            'success': True,
            'module': 'chaos-monkey',
            'action': 'schedule_termination',
            'schedule': schedule,
            'accounts': accounts,
            'message': f'Would schedule terminations for accounts: {accounts}',
            'note': 'This is a simulation. Deploy Chaos Monkey with proper AWS credentials for real chaos.'
        }
    
    def _enable(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Enable Chaos Monkey."""
        self.enabled = True
        
        return {
            'success': True,
            'module': 'chaos-monkey',
            'action': 'enable',
            'message': 'Chaos Monkey enabled',
            'enabled': self.enabled
        }
    
    def _disable(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Disable Chaos Monkey."""
        self.enabled = False
        
        return {
            'success': True,
            'module': 'chaos-monkey',
            'action': 'disable',
            'message': 'Chaos Monkey disabled',
            'enabled': self.enabled
        }
    
    def get_available_actions(self) -> List[str]:
        """Get available Chaos Monkey actions."""
        return ['terminate_instance', 'schedule_termination', 'enable', 'disable']
    
    def get_status(self) -> Dict[str, Any]:
        """Get Chaos Monkey status."""
        return {
            'module': 'chaos-monkey',
            'configured': self.validate_config(),
            'enabled': self.enabled,
            'schedule_enabled': self.schedule_enabled,
            'accounts': self.accounts,
            'regions': self.regions
        }
