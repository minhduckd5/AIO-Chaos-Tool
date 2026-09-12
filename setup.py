#!/usr/bin/env python3
"""
Setup script for ChaosGen — AI-Driven Chaos Scenario Generator.
"""

from setuptools import setup, find_packages
from pathlib import Path

readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

setup(
    name="chaosgen",
    version="0.2.0",
    description="ChaosGen — AI-Driven Chaos Scenario Generator",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="ChaosGen Team",
    author_email="minhduckd5@users.noreply.github.com",
    url="https://github.com/minhduckd5/ChaosGen",
    packages=find_packages(exclude=["aio_chaos_tool*"]),
    install_requires=[
        "PyYAML>=6.0",
        "pydantic>=2.0.0",
        "transitions>=0.9.0",
        "requests>=2.25.0",
        "scikit-learn>=1.3.0",
        "pandas>=2.0.0",
        "numpy>=1.24.0",
        "joblib>=1.3.0",
        # MODIFIED: required by AnomalyDetector.plot_timeline (CI installs
        # .[dev,gui], which previously omitted matplotlib despite requirements.txt).
        "matplotlib>=3.7.0",
        "ollama>=0.3.0",
        "instructor>=1.0.0",
        "Jinja2>=3.1.0",
        # Phase 1 — Discovery Engine
        "kubernetes>=28.0.0",
        "networkx>=3.0",
        # Phase 2 — Bootstrap
        # (no extra runtime deps beyond subprocess + shutil)
        # Phase 3 — Multi-LLM + Secrets
        "openai>=1.0.0",
        "anthropic>=0.20.0",
        "groq",
        "python-dotenv>=1.0.0",
    ],
    extras_require={
        "gui": [
            "PySide6>=6.5.0",
            "PySide6-Fluent-Widgets>=1.5.0",
        ],
        "api": [
            "fastapi>=0.110",
            "uvicorn[standard]>=0.27",
        ],
        "dev": [
            "pytest>=7.0",
            "pytest-cov>=4.0",
            "locust>=2.20.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "chaosgen=chaosgen.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "Topic :: Software Development :: Testing",
        "Topic :: System :: Monitoring",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.10",
)
