#!/usr/bin/env python3
"""
Regression test: bin/version_compatibility_check.py must surface silent errors
via logger.debug at the 3 swallow sites (_notify_macos, _notify_linux,
_notify_windows). Each except block must bind the exception to a name and
call logger.debug, not swallow the traceback with a bare
`except SomeError: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The _notify_macos osascript except binds the exception AND calls logger.debug
4. The _notify_linux notify-send except binds the exception AND calls logger.debug
5. The _notify_windows PowerShell except binds the exception AND calls logger.debug
6. None of the swallow sites is a bare `except ...: pass` (no bound name)

Round 359: Surface silent errors in bin/version_compatibility_check.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/version_compatibility_check.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/version_compatibility_check.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_except_in_func(tree, func_name):
    """Return list of (lineno, handler_node) for ExceptHandlers inside func."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    handlers.append((child.lineno, child))
    return handlers


def test_notify_macos_except_binds_and_logs():
    """_notify_macos's osascript except must bind exception and log at DEBUG."""
    src, tree = _load_source(), ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "_notify_macos")
    assert handlers, "_notify_macos has no except blocks"
    # Find the one referencing osascript or subprocess
    matching = [h for ln, h in handlers if "subprocess" in ast.unparse(h)]
    assert matching, "subprocess except block not found in _notify_macos"
    h = matching[0]
    assert h.name is not None, "_notify_macos except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "_notify_macos except must call logger.debug, not bare `pass`"
    )


def test_notify_linux_except_binds_and_logs():
    """_notify_linux's notify-send except must bind exception and log at DEBUG."""
    src, tree = _load_source(), ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "_notify_linux")
    assert handlers, "_notify_linux has no except blocks"
    # Find the one referencing subprocess
    matching = [h for ln, h in handlers if "subprocess" in ast.unparse(h)]
    assert matching, "subprocess except block not found in _notify_linux"
    h = matching[0]
    assert h.name is not None, "_notify_linux except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "_notify_linux except must call logger.debug, not bare `pass`"
    )


def test_notify_windows_except_binds_and_logs():
    """_notify_windows's PowerShell except must bind exception and log at DEBUG."""
    src, tree = _load_source(), ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "_notify_windows")
    assert handlers, "_notify_windows has no except blocks"
    # Find the one referencing subprocess
    matching = [h for ln, h in handlers if "subprocess" in ast.unparse(h)]
    assert matching, "subprocess except block not found in _notify_windows"
    h = matching[0]
    assert h.name is not None, "_notify_windows except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "_notify_windows except must call logger.debug, not bare `pass`"
    )


def test_no_bare_except_pass_in_target_functions():
    """None of the target functions may have a bare `except ...: pass`."""
    src = _load_source()
    tree = ast.parse(src)
    for func_name in ("_notify_macos", "_notify_linux", "_notify_windows"):
        handlers = _find_except_in_func(tree, func_name)
        for ln, h in handlers:
            # Bare except with just `pass` is disallowed
            if h.name is None:
                body_src = ast.unparse(h)
                assert "pass" not in body_src or "logger.debug" in body_src, (
                    f"{func_name} has a bare except: pass at line {ln}"
                )
