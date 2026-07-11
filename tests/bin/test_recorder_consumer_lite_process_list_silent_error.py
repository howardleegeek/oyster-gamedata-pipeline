#!/usr/bin/env python3
"""
Regression test: recorder_consumer_lite._list_windows_processes silent error surfacing.

Verifies that:
1. Module has a logger imported (module-level)
2. Outer bare except in _list_windows_processes binds exception + logs at DEBUG
3. Inner bare except in CSV parsing binds exception + logs at DEBUG
4. Module compiles without syntax errors

Round 325: Surface silent errors in _list_windows_processes()
"""

import ast
from pathlib import Path


def test_module_has_logger():
    """Verify module imports and defines a logger."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    has_logger_import = False
    has_logger_definition = False
    for node in ast.walk(tree):
        # Check for: import logging
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "logging":
                    has_logger_import = True
        # Check for: logger = logging.getLogger(...)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    if isinstance(node.value, ast.Call):
                        if isinstance(node.value.func, ast.Attribute):
                            if node.value.func.attr == "getLogger":
                                has_logger_definition = True
    assert has_logger_import, "Module must import logging"
    assert has_logger_definition, "Module must define logger = logging.getLogger(__name__)"


def test_list_windows_processes_no_bare_except():
    """Verify _list_windows_processes has no bare except Exception: pass blocks."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_list_windows_processes":
            func_node = node
            break
    assert func_node is not None, "_list_windows_processes function must exist"

    # Check both except handlers are bound (have an 'as' clause)
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            # Outer handler (subprocess.check_output failure)
            if node.type is None:
                raise AssertionError("Bare except: found bare except in _list_windows_processes")
            # Check that exception is bound to a name
            if node.type.id == "Exception" and node.name is None:
                raise AssertionError("Bare except: Exception handler must bind exception (e.g., 'except Exception as e:')")


def test_outer_except_logs_debug():
    """Verify outer except handler logs at DEBUG level."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    assert "except Exception as e:" in src, "Outer exception must be bound to 'e'"
    assert 'logger.debug("_list_windows_processes: tasklist failed:' in src, "Outer except must log at DEBUG"


def test_inner_except_logs_debug():
    """Verify inner CSV parse except handler logs at DEBUG level."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    # Find the inner except (inside the for loop processing lines)
    # Should have exception bound and logger.debug for CSV parse
    lines = src.split("\n")
    in_for_loop = False
    found_inner_debug = False
    for i, line in enumerate(lines):
        if "for line in out.splitlines():" in line:
            in_for_loop = True
        if in_for_loop and 'logger.debug("_list_windows_processes: CSV parse failed' in line:
            found_inner_debug = True
            break
    assert found_inner_debug, "Inner except must log at DEBUG level"


def test_module_compiles():
    """Verify module compiles without syntax errors."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    try:
        compile(src, "bin/recorder_consumer_lite.py", "exec")
    except SyntaxError as e:
        raise AssertionError(f"Module has syntax error: {e}")


if __name__ == "__main__":
    test_module_has_logger()
    test_list_windows_processes_no_bare_except()
    test_outer_except_logs_debug()
    test_inner_except_logs_debug()
    test_module_compiles()
    print("All tests passed.")
