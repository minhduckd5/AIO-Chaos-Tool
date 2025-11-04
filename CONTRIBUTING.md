# Contributing to AIO Chaos Tool

Thank you for your interest in contributing to AIO Chaos Tool! This document provides guidelines and instructions for contributing.

## Getting Started

### Prerequisites

- Python 3.8 or higher
- Git
- pip

### Development Setup

1. Fork the repository on GitHub
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/AIO-Chaos-Tool.git
   cd AIO-Chaos-Tool
   ```
3. Install development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
4. Create a branch for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Guidelines

### Code Style

- Follow PEP 8 style guidelines
- Use meaningful variable and function names
- Add docstrings to all public functions and classes
- Keep functions focused and modular

### Adding a New Chaos Module

To add a new chaos engineering tool integration:

1. **Create the module file** in `aio_chaos_tool/modules/`:
   ```python
   # modules/new_tool.py
   from typing import Dict, Any, List
   from .base import BaseChaosModule

   class NewToolModule(BaseChaosModule):
       def __init__(self, config: Dict[str, Any] = None):
           super().__init__(config)
           # Initialize your tool-specific settings
       
       def validate_config(self) -> bool:
           # Validate configuration
           return True
       
       def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
           # Implement action execution
           pass
       
       def get_available_actions(self) -> List[str]:
           # Return list of actions
           return ['action1', 'action2']
       
       def get_status(self) -> Dict[str, Any]:
           # Return module status
           return {'module': 'new-tool', 'configured': True}
   ```

2. **Register the module** in `orchestrator.py`:
   ```python
   from .modules.new_tool import NewToolModule
   
   MODULE_REGISTRY = {
       # ... existing modules ...
       'new-tool': NewToolModule,
   }
   ```

3. **Add tests** in `tests/`:
   ```python
   def test_new_tool_module():
       orchestrator = ChaosOrchestrator()
       result = orchestrator.execute_action(
           'new-tool',
           'action1',
           {}
       )
       assert result['success'] is True
   ```

4. **Update documentation**:
   - Add the tool to README.md
   - Document available actions
   - Add configuration examples

### Running Tests

Run all tests:
```bash
pytest tests/ -v
```

Run tests with coverage:
```bash
pytest tests/ --cov=aio_chaos_tool --cov-report=html
```

### Testing Your Changes

Before submitting a pull request:

1. Ensure all tests pass:
   ```bash
   pytest tests/ -v
   ```

2. Test CLI commands:
   ```bash
   aio-chaos list-modules
   aio-chaos list-actions --module your-module
   aio-chaos execute --module your-module --action your-action
   ```

3. Verify your module status works:
   ```bash
   aio-chaos status --module your-module
   ```

## Pull Request Process

1. **Update documentation**: Ensure README.md and other docs reflect your changes
2. **Add tests**: New features should have corresponding tests
3. **Pass all tests**: All existing tests must continue to pass
4. **Update CHANGELOG**: Add your changes to the changelog (if applicable)
5. **Create pull request**: 
   - Provide a clear description of the changes
   - Reference any related issues
   - Include examples of the new functionality

### Pull Request Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
Describe how you tested your changes

## Checklist
- [ ] Tests pass locally
- [ ] Documentation updated
- [ ] Code follows style guidelines
- [ ] Added/updated tests for changes
```

## Reporting Issues

### Bug Reports

Include:
- Description of the bug
- Steps to reproduce
- Expected behavior
- Actual behavior
- Python version and OS
- Relevant logs or error messages

### Feature Requests

Include:
- Description of the feature
- Use case and benefits
- Proposed implementation (if any)
- Examples of similar features in other tools

## Code Review Process

1. All pull requests require review
2. Reviewers will check:
   - Code quality and style
   - Test coverage
   - Documentation
   - Breaking changes
3. Address review comments
4. Once approved, maintainers will merge

## Community Guidelines

- Be respectful and constructive
- Follow the code of conduct
- Help others when possible
- Share knowledge and best practices

## Questions?

- Open an issue for questions
- Tag issues with "question" label
- Check existing issues first

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

Thank you for contributing to AIO Chaos Tool!
