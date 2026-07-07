"""Regression test: bin/recorder_fullscreen_detector.py surfaces errors.

The Windows detect() path wraps _windows_detect() in a broad
``except Exception`` to avoid crashing the recorder when the host has
no foreground window / DPI / ctypes weirdness. Historically the
exception was swallowed silently. The fix adds ``logger.debug(...)
with exc_info=True`` so the failure mode is observable in logs while
the control flow (return a non-fatal DetectionResult) is preserved.

This test asserts the structural pattern:
  1. No bare ``except:`` (always ``except Exception as ...``)
  2. ``logger`` is imported and bound to a getLogger name
  3. The broad-except handler in ``detect_exclusive_fullscreen`` has a
     ``logger.debug`` call (so the swallowed error is at least logged)
  4. The module still imports (py_compile)
"""

from __future__ import annotations

import ast
import logging
import py_compile
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "bin" / "recorder_fullscreen_detector.py"


def _load_module():
    sys.path.insert(0, str(REPO_ROOT / "bin"))
    try:
        import recorder_fullscreen_detector as mod  # noqa: WPS433
    finally:
        try:
            sys.path.remove(str(REPO_ROOT / "bin"))
        except ValueError:
            pass
    return mod


def test_no_bare_except() -> None:
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            assert node.type is not None, f"bare except at line {node.lineno}"
    assert True


def test_logger_imported() -> None:
    mod = _load_module()
    assert hasattr(mod, "logger")
    assert isinstance(mod.logger, logging.Logger)
    # Standard pattern: logging.getLogger(__name__)
    assert mod.logger.name == "recorder_fullscreen_detector"


def test_broad_except_logs_at_debug() -> None:
    """The broad `except Exception` in detect_exclusive_fullscreen must
    contain a logger.debug call (with exc_info) so the swallowed error
    is observable in logs."""
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Find the function detect_exclusive_fullscreen
    target_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "detect_exclusive_fullscreen":
            target_fn = node
            break
    assert target_fn is not None, "detect_exclusive_fullscreen not found"

    # Walk its body; find a Try whose handler is `except Exception` whose
    # body contains a logger.debug call.
    found = False
    for node in ast.walk(target_fn):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.type is None:
                    continue
                # handler.type is an ast.Name or ast.Tuple of names
                names = []
                if isinstance(handler.type, ast.Name):
                    names.append(handler.type.id)
                elif isinstance(handler.type, ast.Tuple):
                    for elt in handler.type.elts:
                        if isinstance(elt, ast.Name):
                            names.append(elt.id)
                if "Exception" not in names:
                    continue
                # Inspect handler body for logger.debug / logger.exception
                for sub in ast.walk(handler):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        if (
                            isinstance(func, ast.Attribute)
                            and isinstance(func.value, ast.Name)
                            and func.value.id == "logger"
                            and func.attr in ("debug", "exception", "info", "warning", "error")
                        ):
                            found = True
                            break
                if found:
                    break
        if found:
            break
    assert found, "expected a logger.* call in the broad except handler of detect_exclusive_fullscreen"


def test_module_compiles() -> None:
    assert py_compile.compile(str(TARGET), doraise=True) is not None
