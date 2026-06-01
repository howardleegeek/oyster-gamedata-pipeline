"""Pytest configuration for oyster-agent-runner test suite."""

import pytest

# Configure pytest-asyncio mode
pytest_plugins = ("pytest_asyncio",)


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "asyncio: mark test as async")