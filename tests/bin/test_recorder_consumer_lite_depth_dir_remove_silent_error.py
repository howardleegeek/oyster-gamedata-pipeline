#!/usr/bin/env python3
"""
Regression test: recorder_consumer_lite should surface errors when removing
depth directory after user skip, not swallow them silently.

This test verifies:
1. Module has _trace function (used for logging)
2. The depth dir removal except block binds the exception
3. The except block logs the error via _trace
4. Module compiles without syntax errors

Round 333: Surface silent error in depth directory removal (line ~6704).
"""

import ast
from pathlib import Path


def test_module_has_trace():
    """Verify module defines _trace function."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    has_trace = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_trace":
            has_trace = True
            break
    assert has_trace, "Module must define _trace function for logging"


def test_depth_rm_except_binds_exception():
    """Verify the depth dir removal except block binds the exception."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)

    # Find the section around "depth skipped" + "rmtree"
    # We look for ExceptHandler nodes that have "rmtree" in preceding Try body
    found_bound = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            # Check if this is the rmtree try block
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Attribute):
                        if child.func.attr == "rmtree":
                            # Found rmtree call, now check its ExceptHandler
                            if node.handlers:
                                for handler in node.handlers:
                                    if handler.name is not None:
                                        # Exception is bound to a name
                                        found_bound = True
    assert found_bound, "Depth rmtree except block must bind exception (e.g., 'except Exception as e:')"


def test_depth_rm_except_logs_error():
    """Verify the depth dir removal except block logs the error."""
    src = Path("bin/recorder_consumer_lite.py").read_text()

    # Check that the code contains _trace call in the context of depth skip rmtree
    # We look for a pattern: except ... _trace("depth: ... rmtree ... ")
    import re
    pattern = r"except\s+Exception\s+as\s+\w+:.*?_trace\(f?['\"].*?rmtree.*?['\"]"
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None, "Depth rmtree except block must call _trace with error message"


def test_module_compiles():
    """Verify module compiles without syntax errors."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    try:
        compile(src, "bin/recorder_consumer_lite.py", "exec")
    except SyntaxError as e:
        raise AssertionError(f"Module has syntax error: {e}")
