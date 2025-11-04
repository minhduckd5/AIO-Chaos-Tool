#!/usr/bin/env python3
"""
Command-line interface for AIO Chaos Tool.
"""

import argparse
import json
import sys
from typing import Dict, Any
from pathlib import Path

from .orchestrator import ChaosOrchestrator


def print_json(data: Dict[str, Any]) -> None:
    """Print data as formatted JSON."""
    print(json.dumps(data, indent=2))


def cmd_list_modules(orchestrator: ChaosOrchestrator, args: argparse.Namespace) -> int:
    """List all available chaos modules."""
    modules = orchestrator.list_modules()
    
    print("Available Chaos Modules:")
    print("-" * 40)
    for module in modules:
        print(f"  • {module}")
    
    return 0


def cmd_list_actions(orchestrator: ChaosOrchestrator, args: argparse.Namespace) -> int:
    """List available actions for modules."""
    if args.module:
        # List actions for specific module
        actions = orchestrator.get_module_actions(args.module)
        if not actions:
            print(f"Module not found: {args.module}", file=sys.stderr)
            return 1
        
        print(f"Available Actions for {args.module}:")
        print("-" * 40)
        for action in actions:
            print(f"  • {action}")
    else:
        # List all actions for all modules
        all_actions = orchestrator.get_all_actions()
        print("Available Actions by Module:")
        print("-" * 40)
        for module_name, actions in all_actions.items():
            print(f"\n{module_name}:")
            for action in actions:
                print(f"  • {action}")
    
    return 0


def cmd_status(orchestrator: ChaosOrchestrator, args: argparse.Namespace) -> int:
    """Show status of chaos modules."""
    if args.module:
        # Show status for specific module
        status = orchestrator.get_module_status(args.module)
        print_json(status)
    else:
        # Show status for all modules
        status = orchestrator.get_all_status()
        print_json(status)
    
    return 0


def cmd_execute(orchestrator: ChaosOrchestrator, args: argparse.Namespace) -> int:
    """Execute a chaos action."""
    if not args.module:
        print("Error: --module is required", file=sys.stderr)
        return 1
    
    if not args.action:
        print("Error: --action is required", file=sys.stderr)
        return 1
    
    # Parse parameters
    params = {}
    if args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON parameters: {e}", file=sys.stderr)
            return 1
    
    # Execute action
    result = orchestrator.execute_action(args.module, args.action, params)
    print_json(result)
    
    return 0 if result.get('success', False) else 1


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        description='AIO Chaos Tool - All-In-One Chaos Engineering Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '-c', '--config',
        help='Path to configuration file (YAML or JSON)',
        type=str
    )
    
    parser.add_argument(
        '-v', '--version',
        action='version',
        version='AIO Chaos Tool 0.1.0'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # List modules command
    subparsers.add_parser(
        'list-modules',
        help='List all available chaos modules'
    )
    
    # List actions command
    list_actions_parser = subparsers.add_parser(
        'list-actions',
        help='List available actions'
    )
    list_actions_parser.add_argument(
        '-m', '--module',
        help='Show actions for specific module',
        type=str
    )
    
    # Status command
    status_parser = subparsers.add_parser(
        'status',
        help='Show module status'
    )
    status_parser.add_argument(
        '-m', '--module',
        help='Show status for specific module',
        type=str
    )
    
    # Execute command
    execute_parser = subparsers.add_parser(
        'execute',
        help='Execute a chaos action'
    )
    execute_parser.add_argument(
        '-m', '--module',
        help='Module name',
        type=str,
        required=True
    )
    execute_parser.add_argument(
        '-a', '--action',
        help='Action to execute',
        type=str,
        required=True
    )
    execute_parser.add_argument(
        '-p', '--params',
        help='Action parameters as JSON string',
        type=str
    )
    
    return parser


def main() -> int:
    """Main entry point for CLI."""
    parser = create_parser()
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Initialize orchestrator
    try:
        orchestrator = ChaosOrchestrator(args.config)
    except Exception as e:
        print(f"Error initializing chaos orchestrator: {e}", file=sys.stderr)
        return 1
    
    # Execute command
    commands = {
        'list-modules': cmd_list_modules,
        'list-actions': cmd_list_actions,
        'status': cmd_status,
        'execute': cmd_execute
    }
    
    command_func = commands.get(args.command)
    if command_func:
        return command_func(orchestrator, args)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
