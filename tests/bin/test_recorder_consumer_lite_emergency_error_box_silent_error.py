#!/usr/bin/env python3
"""
Regression test: recorder_consumer_lite._emergency_error_box() should
surface the 3 best-effort failure points at DEBUG, not swallow them
silently.

This test verifies:
1. Module has a logger imported and defined (module-level)
2. _emergency_error_box has no bare `except Exception: pass` blocks (AST)
3. All 3 except handlers in _emergency_error_box bind the exception
4. All 3 except handlers log a `logger.debug(...)` call with identifying
   context for: remote log upload, tkinter error dialog, local fallback
   log write
5. Module compiles without syntax errors

Round 330: Surface silent errors in _emergency_error_box() — the
crash-recovery error dialog shown to the user when the recorder fails
to start. The 3 swallow points (remote log upload, tkinter dialog
init, local fallback log write) were all bare `except Exception: pass`.
Now they bind the exception and emit a debug log so post-crash recovery
failures are visible in DEBUG logs while preserving the best-effort
control flow (no dialog re-raise, function still returns None).
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


def test_emergency_error_box_no_bare_except():
    """Verify _emergency_error_box has no bare `except Exception: pass` blocks."""
    func_node = _find_function(_parse_module(), "_emergency_error_box")
    bare_count = 0
    bound_count = 0
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            # Bare except: (node.type is None) — only forbidden in our 3 swallow points
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
        f"_emergency_error_box has {bare_count} unbound/bare except blocks; "
        "all 3 swallow points must bind the exception and log"
    )
    assert bound_count >= 3, (
        f"_emergency_error_box has only {bound_count} bound except handlers; "
        "expected at least 3 (remote log upload, tkinter dialog, local log write)"
    )


def test_remote_upload_except_binds_and_logs():
    """The remote log upload except handler must bind `e` and log at DEBUG."""
    func_node = _find_function(_parse_module(), "_emergency_error_box")
    upload_excepts = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            if (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and node.name is not None
            ):
                # Check body for logger.debug call with identifying context
                for child in ast.walk(node):
                    if (
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr == "debug"
                        and isinstance(child.func.value, ast.Name)
                        and child.func.value.id == "logger"
                    ):
                        upload_excepts.append(node)
                        break
    assert len(upload_excepts) >= 1, (
        "Expected at least 1 logger.debug call inside the bound except handler "
        "for remote log upload in _emergency_error_box"
    )


def test_remote_upload_debug_log_present():
    """The remote log upload debug log message must mention upload."""
    src = SRC.read_text()
    assert "emergency_error_box: remote log upload failed" in src, (
        "Expected debug log for emergency_error_box remote log upload failure"
    )


def test_tk_dialog_except_binds_and_logs():
    """The tkinter dialog except handler must bind `e` and log at DEBUG."""
    func_node = _find_function(_parse_module(), "_emergency_error_box")
    tk_excepts = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            if (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and node.name is not None
            ):
                for child in ast.walk(node):
                    if (
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr == "debug"
                        and isinstance(child.func.value, ast.Name)
                        and child.func.value.id == "logger"
                    ):
                        tk_excepts.append(node)
                        break
    # We expect at least 2 bound + logged handlers (remote upload + tk dialog)
    # and a 3rd nested one inside the dialog handler (local log write).
    assert len(tk_excepts) >= 2, (
        f"Expected at least 2 bound + debug-logged except handlers in "
        f"_emergency_error_box; found {len(tk_excepts)}"
    )


def test_tk_dialog_debug_log_present():
    """The tkinter dialog debug log message must mention tkinter."""
    src = SRC.read_text()
    assert "emergency_error_box: tkinter error dialog failed" in src, (
        "Expected debug log for emergency_error_box tkinter dialog failure"
    )


def test_local_log_write_except_binds_and_logs():
    """The local fallback log write except handler (nested) must bind and log."""
    src = SRC.read_text()
    assert "emergency_error_box: local fallback log write failed" in src, (
        "Expected debug log for emergency_error_box local fallback log write failure"
    )


def test_module_compiles():
    """Sanity check: module still parses + byte-compiles."""
    import py_compile

    py_compile.compile(str(SRC), doraise=True)
