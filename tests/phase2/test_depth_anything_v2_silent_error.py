"""
Regression test for silent error swallow in src/oyster_agent_runner/phase2/depth_anything_v2.py.

The ``infer_depth()`` function previously had a bare ``except Exception:``
block at line ~127 that did not bind the exception variable. While it
called logger.exception(), the bare except did not comply with the
no-bare-except lint rule.

This test asserts:
  1. No bare ``except Exception:`` (no ``as`` binding) may remain in source.
  2. The module imports ``logging`` and binds a module-level logger.
  3. The except block now binds the exception as ``exc``.
  4. The module compiles cleanly.
  5. Control flow is preserved (still returns False on exception).

Self-review: scope = one file (src/oyster_agent_runner/phase2/depth_anything_v2.py),
one logical change (bind exception as exc), control flow unchanged (still returns
False on failure, still logs via logger.exception).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path
SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

SRC = (SRC_DIR / "oyster_agent_runner" / "phase2" / "depth_anything_v2.py").read_text(
    encoding="utf-8"
)


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
        f"Bind the exception variable."
    )


def test_logger_imported_and_bound() -> None:
    """The module must import logging and bind a module-level logger."""
    assert "import logging" in SRC, "module must import logging"
    assert "logger = logging.getLogger" in SRC, "module must bind logger"


def test_exception_bound_in_infer_depth() -> None:
    """The except block in infer_depth must bind exception as 'exc'."""
    # Check for the pattern "except Exception as exc:"
    assert "except Exception as exc:" in SRC, (
        "infer_depth() must bind exception as 'exc' in except block"
    )


def test_module_compiles() -> None:
    """The module must compile without syntax errors."""
    module_path = SRC_DIR / "oyster_agent_runner" / "phase2" / "depth_anything_v2.py"
    with open(module_path, "r", encoding="utf-8") as f:
        code = f.read()
    compile(code, str(module_path), "exec")


def test_control_flow_preserved() -> None:
    """Control flow must be preserved: still returns False on exception."""
    # The function should still return False on failure
    # We verify the return statement exists after the except block
    assert "return False" in SRC, "infer_depth must return False on exception"
    # The return False should be after the except block
    lines = SRC.split("\n")
    except_line = None
    return_line = None
    for i, line in enumerate(lines):
        if "except Exception as exc:" in line:
            except_line = i
        if "return False" in line and except_line is not None and i > except_line:
            return_line = i
            break
    assert return_line is not None, "return False must follow except block"
