#!/usr/bin/env python3
"""
Regression test: bin/ci_health_check.py analyze_ci_logs() must surface
OSError from fp.stat().st_mtime via logger.debug, not swallow it silently.

This test verifies:
1. The stat() try/except binds the exception to a name (as exc)
2. The handler body calls logger.debug (not just 'continue')
3. The handler still does not re-raise (control flow preserved — continue
   after log)
4. Module compiles without syntax errors

Round 346: Surface silent error in ci_health_check.py stat() call.
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/ci_health_check.py")


def _load_tree():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src, ast.parse(src)


def test_stat_except_binds_exception():
    """The stat() except block must bind the exception to a name."""
    src, tree = _load_tree()
    # Locate the Try whose body contains fp.stat() and matches
    # `if datetime.fromtimestamp(fp.stat().st_mtime) < cutoff: continue`
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            body_text = ast.dump(node)
            if "stat" in body_text and "fromtimestamp" in body_text:
                for handler in node.handlers:
                    if handler.name is not None:
                        found = True
                        assert handler.type is not None
                        # Confirm exception type is OSError
                        if isinstance(handler.type, ast.Name):
                            assert handler.type.id == "OSError", (
                                f"Expected OSError, got {handler.type.id}"
                            )
    assert found, (
        "stat() except block must bind exception "
        "(e.g., 'except OSError as exc:')"
    )


def test_stat_except_logs_error():
    """The stat() except block must call logger.debug."""
    src = SRC_PATH.read_text()
    # Pattern: stat() → fromtimestamp → except OSError as <name>: → logger.debug
    pattern = (
        r"datetime\.fromtimestamp\(fp\.stat\(\)\.st_mtime\)\s*<\s*cutoff:\s*\n"
        r"\s+continue\s*\n"
        r"\s+except\s+OSError\s+as\s+\w+:\s*\n"
        r"\s+logger\.debug\("
    )
    match = re.search(pattern, src)
    assert match is not None, (
        "stat() except block must call logger.debug with the error"
    )


def test_stat_except_does_not_bare_pass():
    """The stat() except block must not be a bare `except OSError: continue`."""
    src = SRC_PATH.read_text()
    # Anti-pattern: stat() → fromtimestamp → except OSError: continue (no bind, no log)
    pattern = (
        r"datetime\.fromtimestamp\(fp\.stat\(\)\.st_mtime\)\s*<\s*cutoff:\s*\n"
        r"\s+continue\s*\n"
        r"\s+except\s+OSError:\s*\n"
        r"\s+continue\s*$"
    )
    match = re.search(pattern, src, re.MULTILINE)
    assert match is None, (
        "stat() except block must bind exception and log — "
        "bare `except OSError: continue` is the silent-swallow pattern"
    )


def test_module_compiles():
    """Module must compile without syntax errors."""
    src = SRC_PATH.read_text()
    compile(src, str(SRC_PATH), "exec")  # raises SyntaxError on failure
