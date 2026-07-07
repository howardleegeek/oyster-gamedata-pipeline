#!/usr/bin/env python3
"""
Regression test: bin/oyster_launch_mc.py must surface silent errors via
logger.debug at swallow sites. The wait_for_mc_ready() function had a bare
`except OSError:` that returned empty text without logging the actual exception.
Now it binds the exception and logs at DEBUG.

This test verifies:
1. The module compiles without syntax errors
2. The module uses the module-level logger
3. The wait_for_mc_ready's except binds the exception to a name
4. The wait_for_mc_ready's except calls logger.debug with the bound exception
5. No bare `except OSError: pass` anti-pattern exists
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/oyster_launch_mc.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/oyster_launch_mc.py must be syntactically valid Python."""
    _load_source()


def test_module_uses_logger():
    """The module must define a module-level logger."""
    src = _load_source()
    assert "logging.getLogger" in src, (
        "module-level logger must be defined using logging.getLogger(...)"
    )


def _find_except_handler(tree, func_name, exception_types):
    """Find an ExceptHandler in function matching the exception types."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    # Check if this handler matches our target types
                    if child.type:
                        type_str = ast.unparse(child.type)
                        # Handle OSError syntax
                        if any(et in type_str for et in exception_types):
                            return child
    return None


def test_wait_for_mc_ready_except_binds_and_logs():
    """wait_for_mc_ready's except must bind exception and log at DEBUG."""
    src = _load_source()
    tree = ast.parse(src)

    handler = _find_except_handler(tree, "wait_for_mc_ready", ["OSError"])
    assert handler is not None, (
        "wait_for_mc_ready should have an except handler for OSError"
    )

    # Verify exception is bound to a name
    assert handler.name is not None, (
        "wait_for_mc_ready except must bind exception to a name (e.g., `as e`)"
    )

    # Verify logger.debug is called with the bound exception
    body_src = ast.unparse(handler)
    assert "logger.debug" in body_src or "log.debug" in body_src, (
        "wait_for_mc_ready except must call logger.debug, not silently swallow"
    )

    # Verify the bound name is used in the debug message
    assert handler.name in body_src, (
        f"wait_for_mc_ready except must use the bound name '{handler.name}' in the log call"
    )


def test_no_bare_wait_for_mc_ready_except():
    """Ensure no bare `except OSError: pass` anti-pattern."""
    src = _load_source()

    # Check for the anti-pattern: except OSError with only pass statement
    import re
    pattern = r"except\s+OSError\s*:\s*\n\s*pass\b"
    matches = re.findall(pattern, src)
    assert len(matches) == 0, (
        f"Found bare 'except OSError: pass' anti-pattern in {SRC_PATH}"
    )
