#!/usr/bin/env python3
"""
Regression test: bin/version_compat_checker.py (G251) must surface silent
errors via logger.debug at the json.JSONDecodeError swallow in
extract_version_from_manifest_text (line ~273). The except block must
bind the exception to a name and call logger.debug, not swallow the
traceback with a bare `except json.JSONDecodeError: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The extract_version_from_manifest_text JSONDecodeError handler binds
   the exception (has a name) AND calls logger.debug
4. The handler is not a bare `except ...: pass`

Round 367: Surface silent error in bin/version_compat_checker.py
extract_version_from_manifest_text JSON manifest parse.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/version_compat_checker.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/version_compat_checker.py must be syntactically valid Python."""
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


def test_extract_version_json_except_binds_and_logs():
    """The JSON manifest parse except in extract_version_from_manifest_text
    must bind the exception and call logger.debug instead of bare `pass`."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "extract_version_from_manifest_text")
    assert handlers, "extract_version_from_manifest_text has no except blocks"
    # Find the JSONDecodeError handler
    matching = [
        h for ln, h in handlers
        if "JSONDecodeError" in ast.unparse(h)
    ]
    assert matching, "JSONDecodeError except block not found in extract_version_from_manifest_text"
    h = matching[0]
    assert h.name is not None, (
        "JSONDecodeError except must bind the exception to a name (e.g. `as exc`)"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "JSONDecodeError except must call logger.debug, not bare `pass`"
    )


def test_no_bare_except_pass_in_extract_version():
    """The extract_version_from_manifest_text function must not contain a
    bare `except SomeError: pass` (no bound name, no log call)."""
    src = _load_source()
    tree = ast.parse(src)
    handlers = _find_except_in_func(tree, "extract_version_from_manifest_text")
    for ln, h in handlers:
        if h.name is None:
            body_src = ast.unparse(h)
            assert "logger.debug" in body_src, (
                f"extract_version_from_manifest_text has an unnamed except at "
                f"line {ln} that doesn't call logger.debug"
            )
