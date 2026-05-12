"""Integration tests for the full Oyster GameData session pipeline.

These tests exercise multiple components together against synthetic but
PRD-compliant session directories. They are slow relative to unit tests
(seconds, not milliseconds) and are gated by the ``integration`` marker
registered in ``pyproject.toml``.

Run only the integration suite::

    pytest -m integration

Skip integration tests (default fast path)::

    pytest -m "not integration"
"""
