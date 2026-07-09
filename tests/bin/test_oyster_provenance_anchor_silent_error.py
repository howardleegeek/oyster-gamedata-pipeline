#!/usr/bin/env python3
"""
Regression test: oyster_provenance/anchor.py must surface silent errors via
logger at the JSON load swallow site in collect_week_manifests() (line ~185).
The except block must bind the exception to a name and call logger, not
swallow the traceback.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The json.load except block binds the exception AND calls logger
4. No bare `except ...: pass` pattern exists in the module

Round 404: Surface silent error in oyster_provenance/anchor.py collect_week_manifests.
"""

import ast
from pathlib import Path

SRC_PATH = Path("oyster_provenance/anchor.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """oyster_provenance/anchor.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_json_load_except(tree):
    """Find the json.load except handler that swallows JSONDecodeError.
    
    The pattern we look for is:
        try:
            ... json.load(...)
        except json.JSONDecodeError as exc:
            logger.debug(...)
    
    We find the ExceptHandler with the right type and verify it binds + logs.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Check if this is the json.load swallow (json.JSONDecodeError)
            if node.type:
                type_str = ast.unparse(node.type)
                if "JSONDecodeError" in type_str:
                    return node
    return None


def test_json_load_except_binds_and_logs():
    """The json.load except must bind exception and call logger."""
    src = _load_source()
    tree = ast.parse(src)
    handler = _find_json_load_except(tree)
    assert handler is not None, "json.JSONDecodeError except handler not found in collect_week_manifests"
    assert handler.name is not None, "json.JSONDecodeError except must bind exception to a name (e.g., 'as exc')"
    body_src = ast.unparse(handler)
    assert "logger." in body_src, (
        "json.JSONDecodeError except must call logger (debug/info/warning/error), "
        "not bare `pass`"
    )


def test_no_bare_except_pass():
    """Verify no bare 'except ...: pass' pattern exists in the module."""
    src = _load_source()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Bare except: pass pattern (only pass in body)
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                # If the exception type is specified, this is the bad pattern
                if node.type is not None:
                    raise AssertionError(
                        f"Bare 'except ...: pass' found at line {node.lineno}: "
                        f"except {ast.unparse(node.type)}: pass"
                    )
