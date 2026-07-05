#!/usr/bin/env python3
"""
Regression test: recorder_consumer_lite._tick_recording_status() should
surface per-tick UI-update errors at DEBUG, not swallow them silently.

This test verifies:
1. Module has a logger imported and defined (module-level)
2. _tick_recording_status has no bare `except Exception: ...` blocks (AST)
3. All 4 except handlers in _tick_recording_status bind the exception
4. All 4 except handlers log a `logger.debug(...)` call with identifying context
5. Module compiles without syntax errors

Round 329: Surface silent errors in _tick_recording_status() — the 1Hz
UI update tick that runs while ffmpeg is recording. The 4 swallow points
(elapsed calc, file size stat, realtime_check, subtitle config) were
all bare `except Exception: pass` (or fallback to 0.0/empty). Now they
bind `e` and emit a debug log so per-tick failures are visible in
DEBUG logs while preserving the visual control flow.
"""

import ast
from pathlib import Path


SRC = Path("bin/recorder_consumer_lite.py")


def _parse_module() -> ast.Module:
    return ast.parse(SRC.read_text())


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def test_module_has_logger():
    """Verify module imports logging and defines a module-level logger."""
    tree = _parse_module()
    has_logger_import = False
    has_logger_definition = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "logging":
                    has_logger_import = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "logger"
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "getLogger"
                ):
                    has_logger_definition = True
    assert has_logger_import, "Module must import logging"
    assert has_logger_definition, "Module must define logger = logging.getLogger(__name__)"


def test_tick_recording_status_no_bare_except():
    """Verify _tick_recording_status has no bare `except Exception: pass` blocks."""
    func_node = _find_function(_parse_module(), "_tick_recording_status")
    bare_count = 0
    bound_count = 0
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            # Bare except: (node.type is None) — only forbidden in our 4 swallow points
            if node.type is None:
                bare_count += 1
                continue
            # except Exception: (not bound) — forbidden
            if (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and node.name is None
            ):
                bare_count += 1
                continue
            bound_count += 1
    assert bare_count == 0, (
        f"_tick_recording_status has {bare_count} bare `except Exception:` "
        "blocks; all 4 should be bound (e.g., 'except Exception as e:')"
    )
    assert bound_count == 4, (
        f"_tick_recording_status should have 4 bound except handlers "
        f"(elapsed, file size, realtime_check, subtitle config); "
        f"got {bound_count}"
    )


def test_elapsed_except_logs_debug():
    """The elapsed-time except handler must log at DEBUG with context."""
    src = SRC.read_text()
    # The new debug log line includes the literal "_tick_recording_status"
    # prefix + "elapsed calc failed"
    assert "_tick_recording_status: elapsed calc failed" in src, (
        "Expected debug log for elapsed calc failure in _tick_recording_status"
    )


def test_file_size_except_logs_debug():
    """The video file size except handler must log at DEBUG with the path."""
    src = SRC.read_text()
    assert "_tick_recording_status: video file size stat failed" in src, (
        "Expected debug log for video file size stat failure"
    )


def test_realtime_check_except_logs_debug():
    """The realtime_check except handler must log at DEBUG."""
    src = SRC.read_text()
    assert "_tick_recording_status: realtime_check failed" in src, (
        "Expected debug log for realtime_check failure"
    )


def test_subtitle_config_except_logs_debug():
    """The subtitle config except handler must log at DEBUG."""
    src = SRC.read_text()
    assert "_tick_recording_status: subtitle config update failed" in src, (
        "Expected debug log for subtitle config update failure"
    )


def test_module_compiles():
    """Sanity check: module still parses + byte-compiles."""
    import py_compile

    py_compile.compile(str(SRC), doraise=True)
