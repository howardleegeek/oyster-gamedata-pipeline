#!/usr/bin/env python3
"""Regression tests: daemon/iter_watcher.py should not silently swallow exceptions."""
import ast
import py_compile
from pathlib import Path


def test_no_bare_except():
    """Module must not contain bare 'except Exception:' without 'as NAME' binding."""
    src = Path("daemon/iter_watcher.py").read_text()
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Bare except or except Exception without binding
            if node.type is None or (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and node.name is None
            ):
                raise AssertionError(
                    f"Bare except at line {node.lineno}: {ast.unparse(node)}"
                )


def test_logger_defined():
    """Module must define a logger for debug logging."""
    src = Path("daemon/iter_watcher.py").read_text()
    tree = ast.parse(src)

    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "log":
                    has_logger = True
                    break
    assert has_logger, "logger must be defined as 'log'"


def test_exception_binding_and_debug_log():
    """run_daemon loop exception handler must bind exception and log at DEBUG."""
    src = Path("daemon/iter_watcher.py").read_text()
    # Verify exception is bound
    assert "except Exception as exc:" in src, "Exception must be bound as 'exc'"
    # Verify debug logging is present
    assert "log.debug" in src, "log.debug must be present for exception logging"


def test_module_compiles():
    """Module must compile without errors."""
    py_compile.compile("daemon/iter_watcher.py", doraise=True)
