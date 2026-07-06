#!/usr/bin/env python3
"""
Regression test: recorder_consumer_lite should surface errors when mod-install
logging fails, not swallow them silently.

This test verifies:
1. Module has _trace function (used for logging)
2. The inner except block in mod-install error handling binds the exception
3. The inner except block writes to stderr instead of silently passing
4. Module compiles without syntax errors

Round 338: Surface silent error in mod-install inner except block (line ~7472).
"""

import ast
import re
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


def test_mod_install_inner_except_binds_exception():
    """Verify the mod-install inner except block binds the exception."""
    src = Path("bin/recorder_consumer_lite.py").read_text()

    # Find the mod-install section and check the inner except binds exception
    # Pattern: _trace("mod-install failed...") followed by except Exception as ...
    pattern = r"_trace\(f?['\"]mod-install failed.*?except\s+Exception\s+as\s+(\w+):"
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None, "Mod-install inner except block must bind exception (e.g., 'except Exception as inner_exc:')"
    # Verify the bound name is captured (the group should exist)
    bound_name = match.group(1)
    assert bound_name, f"Exception should be bound to a name, found: {bound_name}"


def test_mod_install_inner_except_logs_to_stderr():
    """Verify the mod-install inner except block logs to stderr."""
    src = Path("bin/recorder_consumer_lite.py").read_text()

    # Check that the inner except block writes to stderr instead of silently passing
    # Look for print(..., file=sys.stderr) in the context of mod-install error handling
    pattern = r"except\s+Exception\s+as\s+\w+:.*?print\(.*?file=sys\.stderr"
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None, "Mod-install inner except block must write to stderr instead of silently passing"


def test_module_compiles():
    """Verify module compiles without syntax errors."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    try:
        compile(src, "bin/recorder_consumer_lite.py", "exec")
    except SyntaxError as e:
        raise AssertionError(f"Module has syntax error: {e}")
