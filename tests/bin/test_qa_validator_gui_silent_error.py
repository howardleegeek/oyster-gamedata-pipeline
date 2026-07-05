#!/usr/bin/env python3
"""Regression tests: qa_validator_gui.py should not silently swallow exceptions."""
import ast
import py_compile
from pathlib import Path

# Test 1: No bare except blocks
def test_no_bare_except():
    """Module must not contain bare 'except:' or 'except Exception:' without 'as e'."""
    src = Path("bin/qa_validator_gui.py").read_text()
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
    src = Path("bin/qa_validator_gui.py").read_text()
    tree = ast.parse(src)

    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    has_logger = True
                    break
    assert has_logger, "logger must be imported and defined"

# Test 3: emergency_error_box has logger.debug for exception
def test_emergency_error_box_debug_log():
    """_emergency_error_box() must log debug on exception."""
    src = Path("bin/qa_validator_gui.py").read_text()
    assert "except Exception as e:" in src and "_emergency_error_box" in src
    # Look for logger.debug in _emergency_error_box function
    lines = src.split("\n")
    in_function = False
    found_debug = False
    for line in lines:
        if "def _emergency_error_box" in line:
            in_function = True
        elif in_function and line.startswith("def "):
            break
        elif in_function and "logger.debug" in line and "failed" in line.lower():
            found_debug = True
            break
    assert found_debug, "_emergency_error_box must have logger.debug for exception"

# Test 4: Module compiles
def test_module_compiles():
    """Module must compile without errors."""
    py_compile.compile("bin/qa_validator_gui.py", doraise=True)
