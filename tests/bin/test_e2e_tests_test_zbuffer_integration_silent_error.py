#!/usr/bin/env python3
"""Regression tests: test_zbuffer_integration.py should not silently swallow exceptions."""
import ast
from pathlib import Path

# Test 1: No bare except blocks
def test_no_bare_except():
    """Module must not contain bare 'except:' or 'except Exception:' without 'as e'."""
    src = Path("bin/e2e_tests/test_zbuffer_integration.py").read_text()
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Bare except or except Exception without binding
            if node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id == "Exception" and node.name is None
            ):
                raise AssertionError(f"Bare except at line {node.lineno}: {ast.unparse(node)}")


# Test 2: logger is imported
def test_logger_imported():
    """Module must import logger for debug logging."""
    src = Path("bin/e2e_tests/test_zbuffer_integration.py").read_text()
    tree = ast.parse(src)

    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    has_logger = True
                    break
    assert has_logger, "logger must be imported and defined"


# Test 3: find_depth_source_marker has logger.debug for exception
def test_find_depth_source_marker_debug_log():
    """find_depth_source_marker() must log debug on exception."""
    src = Path("bin/e2e_tests/test_zbuffer_integration.py").read_text()
    assert "except Exception as e:" in src
    # Look for logger.debug in find_depth_source_marker function
    lines = src.split("\n")
    in_func = False
    found_debug = False
    for line in lines:
        if "def find_depth_source_marker" in line:
            in_func = True
        elif in_func and line.startswith("def "):
            break
        elif in_func and "logger.debug" in line:
            found_debug = True
            break
    assert found_debug, "find_depth_source_marker must have logger.debug for exception"


# Test 4: run_audit_with_zbuffer_check has logger.debug for exception
def test_run_audit_debug_log():
    """run_audit_with_zbuffer_check() must log debug on exception."""
    src = Path("bin/e2e_tests/test_zbuffer_integration.py").read_text()
    lines = src.split("\n")
    in_func = False
    found_debug = False
    for line in lines:
        if "def run_audit_with_zbuffer_check" in line:
            in_func = True
        elif in_func and line.startswith("def "):
            break
        elif in_func and "logger.debug" in line:
            found_debug = True
            break
    assert found_debug, "run_audit_with_zbuffer_check must have logger.debug for exception"


# Test 5: Module compiles
def test_module_compiles():
    """Module must compile without errors."""
    import py_compile
    py_compile.compile("bin/e2e_tests/test_zbuffer_integration.py", doraise=True)
