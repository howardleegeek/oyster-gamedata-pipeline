#!/usr/bin/env python3
"""Regression tests: error_severity_classifier.py should not silently swallow exceptions."""
import ast
import py_compile
from pathlib import Path


# Test 1: No bare except blocks (except Exception: without 'as e')
def test_no_bare_except():
    """Module must not contain bare 'except:' or 'except Exception:' without 'as e'."""
    src = Path("bin/error_severity_classifier.py").read_text()
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
    src = Path("bin/error_severity_classifier.py").read_text()
    tree = ast.parse(src)

    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    has_logger = True
                    break
    assert has_logger, "logger must be imported and defined"


# Test 3: Exception handler in _load_overrides has logger.warning for exception
def test_load_overrides_logs_warning():
    """_load_overrides() must log warning on exception."""
    src = Path("bin/error_severity_classifier.py").read_text()
    assert "except Exception as exc:" in src, "Exception must be bound as 'exc'"
    assert "logger.warning" in src, "logger.warning must be present"
    # Verify the warning is in the exception handler
    lines = src.split("\n")
    in_except_block = False
    found_warning = False
    for i, line in enumerate(lines):
        if "except Exception as exc:" in line:
            in_except_block = True
        elif in_except_block and line.strip().startswith("def "):
            break
        elif in_except_block and "logger.warning" in line:
            found_warning = True
            break
    assert found_warning, "_load_overrides exception handler must have logger.warning"


# Test 4: Module compiles
def test_module_compiles():
    """Module must compile without errors."""
    py_compile.compile("bin/error_severity_classifier.py", doraise=True)
