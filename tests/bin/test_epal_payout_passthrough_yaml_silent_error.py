"""Regression tests for the silent ImportError swallow in
bin/epal_payout_passthrough.py (yaml optional-import probe).

Background: ``bin/epal_payout_passthrough.py`` probes for PyYAML with a
bare ``except ImportError: pass`` and sets a module-level
``YAML_AVAILABLE`` flag. When PyYAML is missing, the failure is
invisible — the operator has no way to tell *why* YAML inputs/outputs
silently fall back to JSON.

These tests verify:
  1. Static guard: no ``except ImportError: pass`` in the YAML probe.
  2. Behavioural guard: when the import fails, a DEBUG log is emitted
     binding the exception, and ``YAML_AVAILABLE`` is False.
  3. Control-flow guard: when the import succeeds, ``YAML_AVAILABLE`` is
     True (so the behavioural change does not regress the happy path).

Self-review: scope = one file (bin/epal_payout_passthrough.py), one
logical change (bind previously-bare except to ``e`` + log.debug).
"""

from __future__ import annotations

import ast
import importlib
import logging
import sys
from pathlib import Path

import pytest

# Add bin to path so the module is importable.
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))
EPAL_PATH = BIN_DIR / "epal_payout_passthrough.py"
EPAL_SRC = EPAL_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Static guard
# ---------------------------------------------------------------------------


def _find_yaml_probe(tree: ast.Module) -> ast.Try | None:
    """Locate the ``try: import yaml`` block at module level."""
    for node in tree.body:
        if isinstance(node, ast.Try):
            for stmt in node.body:
                if isinstance(stmt, ast.Import):
                    for alias in stmt.names:
                        if alias.name == "yaml":
                            return node
    return None


def test_no_bare_pass_in_yaml_probe():
    """Verify the YAML optional-import probe does not use bare ``pass``."""
    tree = ast.parse(EPAL_SRC)
    probe = _find_yaml_probe(tree)
    assert probe is not None, "could not find `try: import yaml` block"

    for handler in probe.handlers:
        # We only care about ImportError handlers (the YAML probe).
        if not (
            handler.type is not None
            and isinstance(handler.type, ast.Name)
            and handler.type.id == "ImportError"
        ):
            continue
        # A bare pass is a single Pass statement as the handler body.
        if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
            pytest.fail(
                "Found bare `except ImportError: pass` in yaml probe — "
                "should bind the exception and log at debug."
            )


def test_yaml_probe_imports_logging():
    """Verify the module imports `logging` (so the probe can use it)."""
    assert "import logging" in EPAL_SRC, (
        "bin/epal_payout_passthrough.py must import logging so the YAML "
        "probe can bind the ImportError via logger.debug()."
    )


# ---------------------------------------------------------------------------
# Behavioural guard — fake the yaml import to fail
# ---------------------------------------------------------------------------


def test_yaml_import_failure_logs_at_debug(monkeypatch, caplog):
    """When ``import yaml`` raises ImportError, the probe should emit a
    DEBUG log that binds the exception, and YAML_AVAILABLE must be False.
    """
    # Make sure we re-import from scratch so the probe runs again.
    sys.modules.pop("epal_payout_passthrough", None)

    # Force the yaml import inside the module to raise ImportError.
    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "yaml":
            raise ImportError("No module named 'yaml' (forced for test)")
        return importlib.__import__(name, globals, locals, fromlist, level)

    # Patch the import built-in that the module uses (builtins.__import__).
    import builtins as _b
    monkeypatch.setattr(_b, "__import__", _fake_import)

    with caplog.at_level(logging.DEBUG, logger="epal_payout_passthrough"):
        mod = importlib.import_module("epal_payout_passthrough")

    # Control-flow invariant: YAML must still be reported as unavailable.
    assert getattr(mod, "YAML_AVAILABLE", None) is False, (
        "YAML_AVAILABLE must be False when the import fails"
    )

    # The ImportError must be surfaced via a DEBUG log.
    debug_msgs = [
        r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG
    ]
    assert any("yaml" in m.lower() for m in debug_msgs), (
        "Expected a debug log mentioning yaml when the import fails, "
        f"got: {debug_msgs}"
    )


# ---------------------------------------------------------------------------
# Behavioural guard — happy path is preserved
# ---------------------------------------------------------------------------


def test_yaml_import_success_keeps_flag_true():
    """When PyYAML is installed, YAML_AVAILABLE must be True (regression
    guard against accidentally flipping the flag)."""
    sys.modules.pop("epal_payout_passthrough", None)
    import epal_payout_passthrough as mod  # noqa: E402

    # If PyYAML is missing in this environment the import will have
    # raised, in which case the import would have failed before this
    # assertion runs and the test would already have errored.
    assert getattr(mod, "YAML_AVAILABLE", None) is True, (
        "YAML_AVAILABLE should be True when PyYAML is importable"
    )
