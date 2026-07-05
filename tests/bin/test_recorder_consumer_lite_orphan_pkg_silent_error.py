"""Regression test: _package_orphaned_active_session should log exceptions, not swallow silently."""

import ast
import pytest


def test_no_bare_except_in_orphan_package():
    """Verify the orphan package function doesn't use bare except Exception."""
    import sys
    from pathlib import Path

    source = (Path(__file__).parent.parent.parent / "bin" / "recorder_consumer_lite.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find _package_orphaned_active_session function
    target_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_package_orphaned_active_session":
            target_func = node
            break

    assert target_func is not None, "_package_orphaned_active_session function not found"

    # Check for bare except Exception in this function
    bare_excepts = []
    for node in ast.walk(target_func):
        # Look for except handlers without 'as' binding
        if isinstance(node, ast.ExceptHandler):
            if node.type is None or (isinstance(node.type, ast.Name) and node.type.id == "Exception"):
                if node.name is None:
                    bare_excepts.append(node.lineno)

    assert not bare_excepts, f"Bare except Exception found at lines: {bare_excepts}"


def test_logger_imported_for_orphan_package():
    """Verify logger is imported in recorder_consumer_lite.py."""
    import sys
    from pathlib import Path

    source = (Path(__file__).parent.parent.parent / "bin" / "recorder_consumer_lite.py").read_text(encoding="utf-8")

    # Check that logging is imported and logger is defined
    assert "import logging" in source, "logging module not imported"
    assert "logger = logging.getLogger(__name__)" in source, "logger not defined"


def test_orphan_package_logs_exception():
    """Verify the orphan package function logs debug message on exception."""
    import sys
    from pathlib import Path

    source = (Path(__file__).parent.parent.parent / "bin" / "recorder_consumer_lite.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find _package_orphaned_active_session function
    target_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_package_orphaned_active_session":
            target_func = node
            break

    assert target_func is not None, "_package_orphaned_active_session function not found"

    # Check for logger.debug call with exception info
    found_log = False
    for node in ast.walk(target_func):
        if isinstance(node, ast.Call):
            # Check for logger.debug(..., e) or logger.debug(..., exc)
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "debug" and isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "logger":
                        # Check if there's a format string with exception variable
                        for arg in node.args:
                            if isinstance(arg, ast.JoinedStr):  # f-string
                                found_log = True
                            elif isinstance(arg, ast.Name):
                                if arg.id in ("e", "exc", "exception"):
                                    found_log = True

    assert found_log, "logger.debug with exception binding not found in _package_orphaned_active_session"


def test_module_compiles():
    """Verify the module compiles without errors."""
    import py_compile
    from pathlib import Path

    source_path = Path(__file__).parent.parent.parent / "bin" / "recorder_consumer_lite.py"
    py_compile.compile(str(source_path), doraise=True)
