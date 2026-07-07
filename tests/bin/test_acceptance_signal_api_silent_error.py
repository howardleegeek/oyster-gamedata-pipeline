#!/usr/bin/env python3
"""
Regression test: bin/acceptance_signal_api.py must surface silent errors via
logger at the JSON parse swallow site. The except block must bind the
exception to a name and call logger, not swallow the traceback.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The json.JSONDecodeError except block binds the exception AND calls logger
4. No bare `except ...: pass` pattern exists in the module

Round 357: Surface silent errors in bin/acceptance_signal_api.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/acceptance_signal_api.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/acceptance_signal_api.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_json_loads_except(tree):
    """Find the json.loads except handler that swallows JSONDecodeError.

    The pattern we look for is:
        try:
            ... json.loads(...)
        except json.JSONDecodeError as e:
            logger.debug(...)

    We find the ExceptHandler with JSONDecodeError and verify it binds + logs.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type:
                type_str = ast.unparse(node.type)
                if "JSONDecodeError" in type_str:
                    return node
    return None


def test_json_loads_except_binds_and_logs():
    """The json.loads except must bind exception and call logger."""
    src = _load_source()
    tree = ast.parse(src)
    handler = _find_json_loads_except(tree)
    assert handler is not None, "json.JSONDecodeError except handler not found"
    assert handler.name is not None, (
        "json.JSONDecodeError except must bind exception to a name"
    )
    body_src = ast.unparse(handler)
    assert "logger." in body_src, (
        "json.JSONDecodeError except must call logger (debug/info/warning/error), "
        "not bare `pass`"
    )


def test_no_bare_except_pass():
    """Verify no bare 'except: pass' pattern exists in the module."""
    src = _load_source()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Bare except or except with just pass
            if node.type is None or (
                len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
            ):
                raise AssertionError(
                    f"Bare 'except:' or 'except: pass' found at line {node.lineno}"
                )
