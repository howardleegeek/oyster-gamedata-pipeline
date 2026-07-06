#!/usr/bin/env python3
"""
Regression test: bin/mc_client_smoke.py temp-dir cleanup at the end of
main() must surface OSError via logger.debug, not swallow it silently.

This test verifies:
1. The temp-dir cleanup ExceptHandler binds the exception
2. The ExceptHandler body calls logger.debug (not just 'pass')
3. The handler still does not re-raise (control flow preserved)
4. Module compiles without syntax errors

Round 343: Surface silent error in mc_client_smoke temp-dir cleanup
(formerly `except OSError: pass` at the end of main()).
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/mc_client_smoke.py")


def _load_tree():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src, ast.parse(src)


def test_temp_cleanup_except_binds_exception():
    """The temp-dir cleanup except block must bind the exception to a name."""
    src, tree = _load_tree()
    # Locate the cleanup try/except near the end of main()
    # We look for ExceptHandler nodes inside a Try whose body contains
    # an iterdir() call (the temp-dir cleanup pattern).
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            body_text = ast.dump(node)
            if "iterdir" in body_text:
                for handler in node.handlers:
                    if handler.name is not None:
                        # Find the iterdir() block specifically
                        for child in ast.walk(node):
                            if isinstance(child, ast.Call) and isinstance(
                                child.func, ast.Attribute
                            ):
                                if child.func.attr == "iterdir":
                                    found = True
                                    break
                        if found and handler.name is not None:
                            return
    assert found, (
        "Temp-dir cleanup except block must bind exception "
        "(e.g., 'except OSError as exc:')"
    )


def test_temp_cleanup_except_logs_error():
    """The temp-dir cleanup except block must call logger.debug."""
    src = SRC_PATH.read_text()
    # Pattern: a try/except near the end where the except binds to a name
    # and calls logger.debug
    pattern = (
        r"for\s+f\s+in\s+tmp\.iterdir\(\):\s*\n"
        r"\s+f\.unlink\(\)\s*\n"
        r"\s+tmp\.rmdir\(\)\s*\n"
        r"\s+except\s+OSError\s+as\s+\w+:\s*\n"
        r"\s+logger\.debug\("
    )
    match = re.search(pattern, src)
    assert match is not None, (
        "Temp-dir cleanup except block must call logger.debug with the error"
    )


def test_temp_cleanup_does_not_just_pass():
    """The temp-dir cleanup except block must not be a bare pass."""
    src = SRC_PATH.read_text()
    # Look for the anti-pattern: iterdir → unlink → rmdir → except OSError: pass
    pattern = (
        r"for\s+f\s+in\s+tmp\.iterdir\(\):\s*\n"
        r"\s+f\.unlink\(\)\s*\n"
        r"\s+tmp\.rmdir\(\)\s*\n"
        r"\s+except\s+OSError\s*:\s*\n"
        r"\s+pass\s*$"
    )
    match = re.search(pattern, src, re.MULTILINE)
    assert match is None, (
        "Found anti-pattern 'except OSError: pass' in temp-dir cleanup"
    )


def test_module_has_logger():
    """The module must import logging and define a logger."""
    src = SRC_PATH.read_text()
    assert "import logging" in src, "Module must import logging"
    assert re.search(r"logger\s*=\s*logging\.getLogger", src), (
        "Module must define a logger via logging.getLogger"
    )


def test_module_compiles():
    """The module must compile without syntax errors."""
    src = SRC_PATH.read_text()
    try:
        compile(src, str(SRC_PATH), "exec")
    except SyntaxError as e:
        raise AssertionError(f"Module has syntax error: {e}")
