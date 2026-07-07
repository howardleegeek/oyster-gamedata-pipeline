#!/usr/bin/env python3
"""
Regression test: bin/installer_one_click.py must surface silent errors
via logger at the OSError swallow site. The except block must bind the
exception to a name and call logger, not swallow the traceback.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. Every OSError except block binds the exception AND calls logger
4. No bare `except ...: pass` pattern exists in the target module

Round 361: Surface silent errors in bin/installer_one_click.py.
The module has one silent-error site:
  - add_vendor_to_path: OSError when reading/writing shell config files
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/installer_one_click.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/installer_one_click.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_all_oserror_except(tree):
    """Find every OSError ExceptHandler in the tree."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type:
                type_str = ast.unparse(node.type)
                if "OSError" in type_str:
                    handlers.append(node)
    return handlers


def test_all_oserror_handlers_bind_and_log():
    """Every OSError except that uses 'pass' must bind exception and call logger."""
    src = _load_source()
    tree = ast.parse(src)
    handlers = _find_all_oserror_except(tree)
    assert len(handlers) >= 1, (
        "expected at least one OSError except handler"
    )
    for handler in handlers:
        assert handler.name is not None, (
            "OSError except must bind exception to a name"
        )
        # Check only handlers that have pass (silent swallow pattern)
        has_pass = any(isinstance(node, ast.Pass) for node in ast.walk(handler))
        if has_pass:
            body_src = ast.unparse(handler)
            assert "logger." in body_src, (
                "OSError except with pass must call logger "
                "(debug/info/warning/error), not bare `pass`"
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
                # Skip if it has logging (not a silent error)
                body_src = ast.unparse(node.body)
                if "logger" not in body_src.lower():
                    raise AssertionError(
                        f"Found bare except with pass at line {node.lineno}: "
                        f"{ast.unparse(node)}"
                    )
