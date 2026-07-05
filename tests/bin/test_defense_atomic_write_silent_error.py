"""
Regression tests for silent error swallows in src/oyster_agent_runner/defense_atomic_write.py.

This test verifies that the `write_atomic` function's exception handler
binds the exception and logs at DEBUG level, rather than silently swallowing.
Control flow must be preserved: the exception is still raised after cleanup.
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = _REPO_ROOT / "src" / "oyster_agent_runner" / "defense_atomic_write.py"


def test_no_bare_except_in_write_atomic() -> None:
    """All `except Exception:` handlers in write_atomic must bind `e`."""
    src = _SRC.read_text()
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "write_atomic":
            fn = node
            break
    assert fn is not None, "write_atomic function not found"
    for child in ast.walk(fn):
        if isinstance(child, ast.ExceptHandler):
            if child.type is None:
                continue  # bare except (not applicable here)
            type_src = ast.unparse(child.type)
            if "Exception" in type_src:
                assert child.name is not None, (
                    "bare `except Exception:` found at line "
                    f"{child.lineno} in write_atomic — must bind the "
                    "exception as `e` and emit logger.debug(...)"
                )


def test_module_exposes_logger() -> None:
    """The module must define a module-level logger."""
    # Import via importlib to avoid caching
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "defense_atomic_write_under_test", _SRC
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["defense_atomic_write_under_test"] = mod
    spec.loader.exec_module(mod)

    assert hasattr(mod, "_logger"), (
        "defense_atomic_write must expose a module logger"
    )
    assert isinstance(mod._logger, logging.Logger)


def test_exception_binding_and_debug_log(caplog: pytest.LogCaptureFixture) -> None:
    """When write_atomic fails, it logs the bound exception at DEBUG level."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "defense_atomic_write_under_test", _SRC
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["defense_atomic_write_under_test"] = mod
    spec.loader.exec_module(mod)

    write_atomic = mod.write_atomic

    # Mock os.replace to raise an error
    with patch.object(mod.os, "replace", side_effect=OSError("disk full")):
        with caplog.at_level(logging.DEBUG, logger="defense_atomic_write_under_test"):
            with pytest.raises(OSError):
                write_atomic("/tmp/target.txt", "data")

    # Verify debug log contains the bound exception
    assert any(
        "disk full" in record.message and "write_atomic failed" in record.message
        for record in caplog.records
    ), "Expected DEBUG log with bound exception 'disk full'"


def test_control_flow_preserved(caplog: pytest.LogCaptureFixture) -> None:
    """write_atomic still raises the exception after logging (control flow preserved)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "defense_atomic_write_under_test", _SRC
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["defense_atomic_write_under_test"] = mod
    spec.loader.exec_module(mod)

    write_atomic = mod.write_atomic

    # Mock os.replace to raise
    with patch.object(mod.os, "replace", side_effect=ValueError("bad path")):
        with pytest.raises(ValueError) as exc_info:
            write_atomic("/tmp/target.txt", "data")

        assert str(exc_info.value) == "bad path"


def test_module_compiles() -> None:
    """The module must compile without errors."""
    import py_compile

    py_compile.compile(str(_SRC), doraise=True)
