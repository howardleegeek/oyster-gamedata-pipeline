"""Regression test: Surface silent errors in _attempt_mp4_remux_repair()

bin/recorder_consumer_lite.py has 3 bare `except OSError: pass` blocks in
_attempt_mp4_remux_repair() that swallow errors during MP4 remux repair:
1. fixed_path.unlink(missing_ok=True) at line ~956 (initial cleanup)
2. fixed_path.unlink(missing_ok=True) at line ~985 (after ffmpeg fails)
3. fixed_path.unlink(missing_ok=True) at line ~993 (after moov check fails)

This test verifies each handler:
- Binds the exception to a name (not bare `except OSError:`)
- Calls _trace() with the bound exception name
"""

import ast
from pathlib import Path


def test_module_compiles():
    """Module compiles without syntax errors."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    try:
        compile(source, "bin/recorder_consumer_lite.py", "exec")
    except SyntaxError as e:
        raise AssertionError(f"Syntax error in recorder_consumer_lite.py: {e}")
    print("module_compiles: PASS")


def test_function_exists():
    """Target function _attempt_mp4_remux_repair exists."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_attempt_mp4_remux_repair":
            print("function_exists: PASS")
            return
    raise AssertionError("Function _attempt_mp4_remux_repair not found")


def _find_try_except_in_function(tree: ast.AST, func_name: str):
    """Find Try nodes within a specific function."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            # Collect all try/except blocks in this function
            results = []
            for child in ast.walk(node):
                if isinstance(child, ast.Try):
                    results.append(child)
            return results


def test_no_bare_except_oserror_pass_in_target_function():
    """No bare `except OSError: pass` in _attempt_mp4_remux_repair."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)
    try_nodes = _find_try_except_in_function(tree, "_attempt_mp4_remux_repair")

    # Check each try node for bare OSError: pass
    for try_node in try_nodes:
        for handler in try_node.handlers:
            # Check if handler is just `except OSError: pass`
            if handler.type is None:
                continue  # bare except, not OSError-specific
            # Check if handler.type is Name with id='OSError'
            if not (isinstance(handler.type, ast.Name) and handler.type.id == "OSError"):
                continue
            # Check if body is just [Pass]
            if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                raise AssertionError(
                    f"Found bare `except OSError: pass` at line ~{handler.lineno} in "
                    "_attempt_mp4_remux_repair"
                )

    print("no_bare_except_oserror_pass: PASS")


def test_except_binds_exception_in_target_function():
    """Each OSError handler in _attempt_mp4_remux_repair binds the exception."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)
    try_nodes = _find_try_except_in_function(tree, "_attempt_mp4_remux_repair")

    found_binding = False
    for try_node in try_nodes:
        for handler in try_node.handlers:
            # Check if handler.type is Name with id='OSError'
            if not (isinstance(handler.type, ast.Name) and handler.type.id == "OSError"):
                continue
            # Check if it binds to a name (has "as X")
            if handler.name is not None:
                found_binding = True

    if not found_binding:
        raise AssertionError(
            "No OSError handler in _attempt_mp4_remux_repair binds exception to a name"
        )

    print("except_binds_exception: PASS")


def test_except_calls_trace_in_target_function():
    """Each OSError handler in _attempt_mp4_remux_repair calls _trace."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)
    try_nodes = _find_try_except_in_function(tree, "_attempt_mp4_remux_repair")

    found_trace_call = False
    for try_node in try_nodes:
        for handler in try_node.handlers:
            # Check if handler.type is Name with id='OSError'
            if not (isinstance(handler.type, ast.Name) and handler.type.id == "OSError"):
                continue
            # Check if body contains a call to _trace
            for stmt in handler.body:
                for child in ast.walk(stmt):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name) and child.func.id == "_trace":
                            found_trace_call = True

    if not found_trace_call:
        raise AssertionError(
            "No OSError handler in _attempt_mp4_remux_repair calls _trace()"
        )

    print("except_calls_trace: PASS")


def test_trace_references_bound_name():
    """_trace() calls in OSError handlers reference the bound exception name."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)
    try_nodes = _find_try_except_in_function(tree, "_attempt_mp4_remux_repair")

    found_ref = False
    for try_node in try_nodes:
        for handler in try_node.handlers:
            # Check if handler.type is Name with id='OSError'
            if not (isinstance(handler.type, ast.Name) and handler.type.id == "OSError"):
                continue
            # Skip if no binding
            if handler.name is None:
                continue
            bound_name = handler.name
            # Check if any _trace call references the bound name
            for stmt in handler.body:
                stmt_source = ast.unparse(stmt)
                if bound_name in stmt_source:
                    found_ref = True

    if not found_ref:
        raise AssertionError(
            "No _trace() call in OSError handlers references the bound exception name"
        )

    print("trace_references_bound_name: PASS")


if __name__ == "__main__":
    test_module_compiles()
    test_function_exists()
    test_no_bare_except_oserror_pass_in_target_function()
    test_except_binds_exception_in_target_function()
    test_except_calls_trace_in_target_function()
    test_trace_references_bound_name()
    print("\nAll 6 tests passed!")
