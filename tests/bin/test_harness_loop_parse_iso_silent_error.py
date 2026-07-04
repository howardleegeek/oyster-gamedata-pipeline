"""Regression test for the silent-error swallow in
`bin/harness_loop.py` `_parse_iso`.

The handler used to be `except Exception:` which silently dropped 
parse failures. The fix replaces it with a `log.debug(...)` call 
that binds the exception so failures are visible in daemon logs 
(at DEBUG level) without changing the control flow (still returns 0.0).

Checks:
  1. Static guard: the `_parse_iso` Exception handler body must NOT 
     be a bare `return 0.0`.
  2. Static guard: the same handler body must include `log.debug` 
     to surface the swallow.
  3. Behavioural guard: _parse_iso with invalid input produces a 
     debug log and returns 0.0.
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

SRC_PATH = BIN_DIR / "harness_loop.py"


def _find_parse_iso_handler() -> ast.ExceptHandler:
    """Return the Exception handler in _parse_iso."""
    tree = ast.parse(SRC_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_iso":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    # The handler should be `except Exception:`
                    type_text = ast.unparse(child.type) if child.type else "bare"
                    if "Exception" in type_text:
                        return child
    pytest.fail("Could not find except Exception handler in _parse_iso")


def test_no_bare_return_in_parse_iso() -> None:
    """_parse_iso Exception handler must not be a bare return."""
    handler = _find_parse_iso_handler()
    handler_src = ast.unparse(handler)
    # The body should have log.debug before return
    if handler_src.strip().endswith("return 0.0"):
        # Check if there's only a return statement in the body
        if len(handler.body) == 1 and isinstance(handler.body[0], ast.Return):
            pytest.fail(
                "Found bare `return 0.0` in _parse_iso except handler. "
                "Should use log.debug(...) to bind the exception."
            )


def test_parse_iso_handler_logs_via_debug() -> None:
    """_parse_iso Exception handler must include a log.debug call."""
    handler = _find_parse_iso_handler()
    handler_src = ast.unparse(handler)
    has_log_debug = "log.debug" in handler_src
    has_logging_debug = "logging.debug" in handler_src
    if not (has_log_debug or has_logging_debug):
        pytest.fail(
            "Found no log.debug(...) call in _parse_iso except handler. "
            "Should surface the exception for debugging."
        )


def test_parse_iso_returns_zero_on_invalid_input(caplog: pytest.LogCaptureFixture) -> None:
    """_parse_iso returns 0.0 on invalid input and logs the error at DEBUG."""
    caplog.set_level(logging.DEBUG)
    import importlib.util
    spec = importlib.util.spec_from_file_location("harness_loop", SRC_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    result = module._parse_iso("not-a-valid-timestamp")
    assert result == 0.0
    
    # Should have logged the error
    assert any("parse" in record.message.lower() or "timestamp" in record.message.lower() 
               for record in caplog.records), \
        "Expected a debug log about the parse failure"
