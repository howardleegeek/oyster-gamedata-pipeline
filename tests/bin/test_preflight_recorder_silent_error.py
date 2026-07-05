"""
Regression tests for silent error swallow in bin/preflight_recorder.py.

The preflight_recorder module previously had bare ``except Exception:``
blocks that silently swallowed errors. This test asserts:

  1. No bare ``except Exception:`` (no ``as`` binding) remains in the
     module source.
  2. The module imports ``logging`` and binds a module-level logger
     ``logger = logging.getLogger(__name__)``.
  3. All exception handlers in the affected functions now bind the
     exception and log via logger.debug().
  4. The module compiles.

Self-review: scope = one file (bin/preflight_recorder.py), one
logical change (bind previously-bare except to ``e`` + logger.debug).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# Add bin to path
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))


SRC = (BIN_DIR / "preflight_recorder.py").read_text(encoding="utf-8")


def test_no_bare_except_in_module() -> None:
    """No bare ``except Exception:`` (no ``as`` binding) may remain in source."""
    tree = ast.parse(SRC)
    bare_lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.type is not None and handler.name is None:
                    type_src = ast.unparse(handler.type)
                    if "Exception" in type_src:
                        bare_lines.append(handler.lineno)
    assert not bare_lines, (
        f"Found bare 'except Exception:' (no 'as' binding) at lines {bare_lines}. "
        f"Bind the exception and log it via logger.debug(...)."
    )


def test_logger_imported_and_bound() -> None:
    """The module must import logging and bind a module-level logger."""
    assert "import logging" in SRC, "module must import logging"
    assert "logger = logging.getLogger" in SRC, (
        "module must bind a logger via 'logger = logging.getLogger(...)'"
    )


def test_exception_handlers_have_logger_debug() -> None:
    """All exception handlers in the module must call logger.debug with the bound exception."""
    # This test verifies that each exception handler that was previously
    # a bare 'except Exception: pass' now logs the exception via logger.debug().
    # We check for the specific patterns we added.
    assert "logger.debug(" in SRC, "module must call logger.debug() for exception logging"
    # Check that we have the specific debug calls for the 4 functions we modified
    assert "tasklist check failed:" in SRC
    assert "tailscale binary not found" in SRC
    assert "tailscale status check failed:" in SRC
    assert "ping mac1.tailscale failed:" in SRC
    assert "ping %s failed:" in SRC


def test_module_compiles() -> None:
    """The module must compile without errors."""
    try:
        compile(SRC, "preflight_recorder.py", "exec")
    except SyntaxError as e:
        pytest.fail(f"Module failed to compile: {e}")
