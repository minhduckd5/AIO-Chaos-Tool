#!/usr/bin/env python3
"""
Setup script for AIO Chaos Tool.
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

setup(
    name="aio-chaos-tool",
    version="0.1.0",
    description="All-In-One Chaos Engineering Tool",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="AIO Chaos Tool Team",
    author_email="minhduckd5@users.noreply.github.com",
    url="https://github.com/minhduckd5/AIO-Chaos-Tool",
    packages=find_packages(),
    install_requires=[
        "PyYAML>=6.0",
    ],
    extras_require={
        'dev': [
            'pytest>=7.0',
            'pytest-cov>=4.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'aio-chaos=aio_chaos_tool.cli:main',
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "Topic :: Software Development :: Testing",
        "Topic :: System :: Monitoring",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.8",
)
