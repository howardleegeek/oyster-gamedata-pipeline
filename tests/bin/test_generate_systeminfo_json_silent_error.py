"""Regression test: bin/generate_systeminfo_json.py surfaces errors.

The file has 3 historical swallow sites where exceptions were silently
dropped with bare ``except SomeError: pass``:

  1. ``detect_screen_dpi`` (line 50): xrandr ``scale factor: 1.5`` parser
     that drops ValueError when the line is unparseable.
  2. ``detect_screen_dpi`` (line 52): outer catch for the whole probe
     block (subprocess failure / file not found / value error) — used to
     fall through to the default DPI without any log trail.
  3. ``detect_window_geometry`` (line 110): xdotool ``getwindowgeometry``
     outer catch — used to silently fall through to the 1920x1080 default
     even when the host was missing xdotool.

The fix binds the exception name (``as e``) and emits ``logger.debug(...)``
at each site so the failure mode is observable in logs, while preserving
control flow (each site still returns a sane default).

This test asserts the structural pattern:
  1. ``logging`` is imported and a module-level ``logger`` exists
  2. All 3 historical swallow sites bind the exception name (``h.name is not None``)
  3. All 3 sites call ``logger.debug`` (so the swallowed error is logged)
  4. No bare ``except SomeError: pass`` pattern remains at the 3 sites
  5. The module compiles (py_compile)
"""

from __future__ import annotations

import ast
import logging
import py_compile
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "bin" / "generate_systeminfo_json.py"

# Anchor line numbers (will shift slightly if the file is refactored, but
# the pattern is: parse ValueError < outer SubprocessError < xdotool outer).
SITE_LINES = (50, 52, 111)


def _load_module():
    sys.path.insert(0, str(REPO_ROOT / "bin"))
    try:
        import generate_systeminfo_json as mod  # noqa: WPS433
    finally:
        try:
            sys.path.remove(str(REPO_ROOT / "bin"))
        except ValueError:
            pass
    return mod


def test_module_compiles() -> None:
    """bin/generate_systeminfo_json.py must be syntactically valid Python."""
    py_compile.compile(str(TARGET), doraise=True)


def test_logging_imported_and_logger_defined() -> None:
    """The module must import logging and define a module-level `logger`."""
    mod = _load_module()
    assert hasattr(mod, "logger"), "module must define a `logger` attribute"
    assert isinstance(mod.logger, logging.Logger), "`logger` must be a logging.Logger"
    # Standard pattern: logging.getLogger(__name__)
    assert mod.logger.name == "generate_systeminfo_json", (
        "logger must be bound to logging.getLogger(__name__)"
    )


def test_swallow_sites_bind_exception_and_log() -> None:
    """Each of the 3 swallow sites must bind the exception AND call logger.debug."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))

    # Build a map of lineno -> ExceptHandler for fast lookup.
    handler_by_line = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            handler_by_line[node.lineno] = node

    for ln in SITE_LINES:
        assert ln in handler_by_line, (
            f"expected an except handler at line {ln}; found at "
            f"{sorted(handler_by_line)}"
        )
        handler = handler_by_line[ln]
        assert handler.name is not None, (
            f"line {ln}: except must bind the exception name (was None — "
            "silent swallow)"
        )
        body_src = ast.unparse(handler)
        assert "logger.debug" in body_src, (
            f"line {ln}: except must call logger.debug; got: {body_src!r}"
        )
        # Body must NOT be a single `pass` statement.
        if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
            raise AssertionError(
                f"line {ln}: except body is bare `pass` — silent swallow"
            )


def test_no_bare_except_in_module() -> None:
    """No bare ``except:`` may exist anywhere in the module."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            assert node.type is not None, (
                f"bare except at line {node.lineno}"
            )
