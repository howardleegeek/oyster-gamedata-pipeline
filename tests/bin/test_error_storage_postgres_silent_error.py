#!/usr/bin/env python3
"""Regression tests: error_storage_postgres.py should not silently swallow exceptions."""
import ast
from pathlib import Path

# Test 1: No bare except blocks
def test_no_bare_except():
    """Module must not contain bare 'except:' or 'except Exception:' without 'as e'."""
    src = Path("bin/error_storage_postgres.py").read_text()
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
    src = Path("bin/error_storage_postgres.py").read_text()
    tree = ast.parse(src)

    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    has_logger = True
                    break
    assert has_logger, "logger must be imported and defined"

# Test 3: insert_error has logger.debug for exception
def test_insert_error_debug_log():
    """insert_error() must log debug on exception."""
    src = Path("bin/error_storage_postgres.py").read_text()
    assert "except Exception as e:" in src and "insert_error" in src
    # Look for logger.debug in insert_error function
    lines = src.split("\n")
    in_insert_error = False
    found_debug = False
    for line in lines:
        if "def insert_error" in line:
            in_insert_error = True
        elif in_insert_error and line.startswith("def "):
            break
        elif in_insert_error and "logger.debug" in line and "exception" in line.lower():
            found_debug = True
            break
    assert found_debug, "insert_error must have logger.debug for exception"

# Test 4: purge_old_errors has logger.debug for exception
def test_purge_debug_log():
    """purge_old_errors() must log debug on exception."""
    src = Path("bin/error_storage_postgres.py").read_text()
    assert "except Exception as e:" in src and "purge_old_errors" in src
    lines = src.split("\n")
    in_purge = False
    found_debug = False
    for line in lines:
        if "def purge_old_errors" in line:
            in_purge = True
        elif in_purge and line.startswith("def "):
            break
        elif in_purge and "logger.debug" in line and "exception" in line.lower():
            found_debug = True
            break
    assert found_debug, "purge_old_errors must have logger.debug for exception"

# Test 5: Module compiles
def test_module_compiles():
    """Module must compile without errors."""
    import py_compile
    py_compile.compile("bin/error_storage_postgres.py", doraise=True)
