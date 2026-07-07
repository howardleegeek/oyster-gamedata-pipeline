#!/usr/bin/env python3
"""Regression tests: mc_launcher_real.py wait_for_join() should not silently swallow exceptions."""
import ast
import py_compile
from pathlib import Path


# Test 1: Module compiles cleanly
def test_module_compiles():
    """bin/mc_launcher_real.py must compile without syntax errors."""
    py_compile.compile("bin/mc_launcher_real.py", doraise=True)


# Test 2: Module-level logger is defined
def test_logger_defined():
    """Module must define a module-level logger for debug logging."""
    src = Path("bin/mc_launcher_real.py").read_text()
    tree = ast.parse(src)

    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    has_logger = True
                    break
    assert has_logger, "logger must be defined at module level"


# Test 3: logging is imported
def test_logging_imported():
    """Module must import the logging stdlib module."""
    src = Path("bin/mc_launcher_real.py").read_text()
    assert "import logging" in src, "logging must be imported"


# Test 4: wait_for_join() does not have silent except (IOError, OSError): pass
def test_wait_for_join_binds_exception():
    """wait_for_join() IOError/OSError handler must bind exception, not silently pass."""
    src = Path("bin/mc_launcher_real.py").read_text()
    tree = ast.parse(src)

    # Find the wait_for_join function
    wait_for_join = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "wait_for_join":
            wait_for_join = node
            break
    assert wait_for_join is not None, "wait_for_join function must exist"

    # Find any except handler that catches IOError or OSError
    has_unbound = False
    for node in ast.walk(wait_for_join):
        if isinstance(node, ast.ExceptHandler):
            # Get exception type names
            type_names = []
            if node.type is None:
                type_names.append("bare")
            elif isinstance(node.type, ast.Name):
                type_names.append(node.type.id)
            elif isinstance(node.type, ast.Tuple):
                for elt in node.type.elts:
                    if isinstance(elt, ast.Name):
                        type_names.append(elt.id)
            if "IOError" in type_names or "OSError" in type_names:
                if node.name is None:
                    has_unbound = True
                    raise AssertionError(
                        f"wait_for_join() line {node.lineno}: {type_names} handler must bind exception (no silent pass)"
                    )
    assert not has_unbound, "wait_for_join() must bind exception in IOError/OSError handler"


# Test 5: logger.debug is called in wait_for_join
def test_wait_for_join_logs_debug():
    """wait_for_join() must call logger.debug inside the IOError/OSError handler."""
    src = Path("bin/mc_launcher_real.py").read_text()
    tree = ast.parse(src)

    wait_for_join = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "wait_for_join":
            wait_for_join = node
            break
    assert wait_for_join is not None

    # Search for logger.debug call inside the function body
    for node in ast.walk(wait_for_join):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "debug":
                if isinstance(func.value, ast.Name) and func.value.id == "logger":
                    return  # found
    raise AssertionError("wait_for_join() must call logger.debug somewhere in its body")


# Test 6: No bare 'except: pass' pattern in the module
def test_no_silent_pass_except():
    """No 'except ... : pass' pattern should exist in mc_launcher_real.py."""
    src = Path("bin/mc_launcher_real.py").read_text()
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                type_repr = ast.unparse(node) if node.type else "except:"
                raise AssertionError(
                    f"Silent 'except {type_repr}: pass' at line {node.lineno}"
                )
