"""
Basic tests for AIO Chaos Tool modules.
"""

import pytest
from chaosgen.orchestrator import ChaosOrchestrator


def test_orchestrator_initialization():
    """Test that orchestrator initializes correctly."""
    orchestrator = ChaosOrchestrator()
    assert orchestrator is not None


def test_list_modules():
    """Test listing all modules."""
    orchestrator = ChaosOrchestrator()
    modules = orchestrator.list_modules()
    
    expected_modules = [
        'chaos-toolkit',
        'kube-monkey',
        'pumba',
        'chaos-monkey',
        'toxiproxy',
        'muxy'
    ]
    
    assert set(modules) == set(expected_modules)


def test_get_module_actions():
    """Test getting module actions."""
    orchestrator = ChaosOrchestrator()
    
    # Test Pumba actions
    pumba_actions = orchestrator.get_module_actions('pumba')
    assert 'kill_container' in pumba_actions
    assert 'delay_network' in pumba_actions
    
    # Test Toxiproxy actions
    toxiproxy_actions = orchestrator.get_module_actions('toxiproxy')
    assert 'add_latency' in toxiproxy_actions
    assert 'create_proxy' in toxiproxy_actions


def test_execute_action():
    """Test executing an action."""
    orchestrator = ChaosOrchestrator()
    
    result = orchestrator.execute_action(
        'pumba',
        'kill_container',
        {'container': 'test-container'}
    )
    
    assert result['success'] is True
    assert result['module'] == 'pumba'
    assert result['action'] == 'kill_container'


def test_module_status():
    """Test getting module status."""
    orchestrator = ChaosOrchestrator()
    
    status = orchestrator.get_module_status('pumba')
    assert status['module'] == 'pumba'
    assert 'configured' in status


def test_invalid_module():
    """Test handling of invalid module."""
    orchestrator = ChaosOrchestrator()
    
    result = orchestrator.execute_action(
        'invalid-module',
        'some-action',
        {}
    )
    
    assert result['success'] is False
    assert 'error' in result


def test_invalid_action():
    """Test handling of invalid action."""
    orchestrator = ChaosOrchestrator()
    
    result = orchestrator.execute_action(
        'pumba',
        'invalid-action',
        {}
    )
    
    assert result['success'] is False
    assert 'error' in result
