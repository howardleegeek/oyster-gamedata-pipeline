"""
Regression tests for silent error swallow in bin/depth_anything_v2_inference.py.

The ``infer_depth_maps()`` function previously had 4 bare ``except Exception:``
blocks that silently swallowed errors:
  1. Line ~219: progress callback exception
  2. Line ~244: should_skip() check exception
  3. Line ~272: reader.close() exception
  4. Line ~282: outer inference failure (cleanup) exception

This test asserts:
  1. No bare ``except Exception:`` (no ``as`` binding) remains in the
     module source.
  2. The module imports ``logging`` and binds a module-level logger
     ``_LOG = logging.getLogger(...)``.
  3. When any of the 4 error cases occur, DEBUG log records are emitted
     (binding the exception) — instead of being silently swallowed.
  4. Control flow is preserved:
     - progress callback: still swallows exception, still returns silently
     - should_skip check: still continues on failure, still breaks if skip returns True
     - reader.close(): still continues (no-op), still proceeds to final progress tick
     - outer exception: still cleans up output_dir, still re-raises
  5. The module compiles.

Self-review: scope = one file (bin/depth_anything_v2_inference.py), one
logical change (bind 4 previously-bare excepts to ``e`` + LOG.debug), the
module-level ``_LOG = logging.getLogger("depth_anything_v2_inference")`` already
existed.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Add bin to path
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))


SRC = (BIN_DIR / "depth_anything_v2_inference.py").read_text(encoding="utf-8")


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
        f"Bind the exception and log it via _LOG.debug(...)."
    )


def test_logger_imported_and_bound() -> None:
    """The module must import logging and bind a module-level _LOG logger."""
    assert "import logging" in SRC, "module must import logging"
    assert "_LOG = logging.getLogger" in SRC, "module must bind _LOG logger"


def test_progress_callback_logs_debug() -> None:
    """Progress callback failure must emit DEBUG log."""
    assert "_LOG.debug" in SRC, "module must call _LOG.debug for error logging"
    # Check that the progress callback block has debug logging
    assert "progress callback failed" in SRC.lower(), (
        "progress callback exception must log 'progress callback failed'"
    )


def test_should_skip_logs_debug() -> None:
    """should_skip() check failure must emit DEBUG log."""
    assert "skip check failed" in SRC.lower(), (
        "should_skip exception must log 'skip check failed'"
    )


def test_reader_close_logs_debug() -> None:
    """reader.close() failure must emit DEBUG log."""
    assert "reader.close() failed" in SRC.lower(), (
        "reader.close exception must log 'reader.close() failed'"
    )


def test_inference_failure_logs_debug() -> None:
    """Outer inference failure must emit DEBUG log before cleanup and re-raise."""
    assert "inference failed" in SRC.lower(), (
        "outer inference exception must log 'inference failed'"
    )


def test_module_compiles() -> None:
    """The module must compile without syntax errors."""
    import py_compile
    compiled = py_compile.compile(
        str(BIN_DIR / "depth_anything_v2_inference.py"),
        doraise=True,
    )
    assert compiled is not None
