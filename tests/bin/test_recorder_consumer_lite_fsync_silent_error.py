#!/usr/bin/env python3
"""
Regression test: bin/recorder_consumer_lite.py::_fsync_file must surface
silent OSError via logger.debug instead of bare `except OSError: pass`.

Round 351: Surface silent error in bin/recorder_consumer_lite.py::_fsync_file.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The _fsync_file's OSError except binds the exception AND calls logger.debug
4. No bare `except OSError: pass` pattern in _fsync_file
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/recorder_consumer_lite.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/recorder_consumer_lite.py must be syntactically valid Python."""
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


def test_fsync_file_oserror_except_binds_and_logs():
    """_fsync_file's OSError except must bind exception and log at DEBUG."""
    src, tree = _load_source(), ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "_fsync_file")
    assert handlers, "_fsync_file has no except blocks"
    # _fsync_file only has one except block - check it binds and logs
    h = handlers[0][1]
    assert h.name is not None, "_fsync_file except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "_fsync_file except must call logger.debug, not bare `pass`"
    )


def test_fsync_file_no_bare_pass():
    """_fsync_file must not have a bare `except OSError: pass` pattern."""
    src = _load_source()
    # Find the _fsync_file function
    tree = ast.parse(src)
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_fsync_file":
            func_node = node
            break
    assert func_node is not None, "_fsync_file function not found"
    func_src = ast.unparse(func_node)
    # Check that there's no bare "except OSError:\n        pass" pattern
    assert "except OSError:\n        pass" not in func_src, (
        "_fsync_file must not have bare `except OSError: pass`"
    )
