"""
Regression tests for silent error swallow in bin/marketplace_sync.py.

The marketplace_sync module previously had a bare ``except ValueError: pass``
swallow in the FilterParser._parse_condition method (line ~88). This test asserts:

  1. The module imports ``logging`` and binds a module-level logger.
  2. The exception handler in _parse_condition now binds the exception and logs.
  3. No bare ``except ValueError: pass`` remains in the module source.
  4. The module compiles.

Self-review: scope = one file (bin/marketplace_sync.py), one
logical change (bind exception + logger.debug in _parse_condition).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# Add bin to path
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))


SRC = (BIN_DIR / "marketplace_sync.py").read_text(encoding="utf-8")


def test_logger_imported_and_bound() -> None:
    """The module must import logging and bind a module-level logger."""
    assert "import logging" in SRC, "module must import logging"
    assert "logger = logging.getLogger" in SRC or "logger = logging.getLogger(__name__)" in SRC, (
        "module must bind a logger via 'logger = logging.getLogger(...)'"
    )


def test_exception_handler_binds_and_logs() -> None:
    """Exception handler in _parse_condition must bind exception and log via logger.debug."""
    # Check that we have the binding pattern
    assert "except ValueError as exc:" in SRC, (
        "exception handler must bind the exception: 'except ValueError as exc:'"
    )
    # Check that we log via logger.debug with the bound exception
    assert "logger.debug(" in SRC, "module must call logger.debug() for exception logging"
    assert "could not be parsed as int or float" in SRC, (
        "logger.debug must include descriptive message about parse failure"
    )


def test_no_bare_except_value_error_pass() -> None:
    """No bare ``except ValueError: pass`` may remain in source."""
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                # Check for bare ValueError with just pass
                if handler.type is not None:
                    type_src = ast.unparse(handler.type)
                    if "ValueError" in type_src:
                        # Check if body is just 'pass'
                        if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                            pytest.fail(
                                f"Found bare 'except ValueError: pass' at line {handler.lineno}. "
                                f"Bind the exception and log it via logger.debug()."
                            )


def test_module_compiles() -> None:
    """The module must compile without errors."""
    try:
        compile(SRC, "marketplace_sync.py", "exec")
    except SyntaxError as e:
        pytest.fail(f"Module failed to compile: {e}")
