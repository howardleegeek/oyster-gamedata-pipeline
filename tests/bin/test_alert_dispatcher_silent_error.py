#!/usr/bin/env python3
"""
Regression test: bin/alert_dispatcher.py must surface silent errors via
logger.debug at swallow sites. The _time_ago() method had a bare
`except (ValueError, TypeError):` that returned "unknown" without logging
the actual exception. Now it binds the exception and logs at DEBUG.

This test verifies:
1. The module compiles without syntax errors
2. The module uses the module-level logger `log`
3. The _time_ago's except binds the exception to a name
4. The _time_ago's except calls log.debug with the bound exception
5. No bare `except (ValueError, TypeError): pass` anti-pattern exists
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/alert_dispatcher.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/alert_dispatcher.py must be syntactically valid Python."""
    _load_source()


def test_module_uses_log():
    """The module must define a module-level logger `log`."""
    src = _load_source()
    assert "log = logging.getLogger" in src, (
        "module-level logger must be defined as `log = logging.getLogger(...)`"
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
                        # Handle (ValueError, TypeError) syntax
                        if any(et in type_str for et in exception_types):
                            return child
    return None


def test_time_ago_except_binds_and_logs():
    """_time_ago's except must bind exception and log at DEBUG."""
    src = _load_source()
    tree = ast.parse(src)

    handler = _find_except_handler(tree, "_time_ago", ["ValueError", "TypeError"])
    assert handler is not None, (
        "_time_ago should have an except handler for ValueError/TypeError"
    )

    # Verify exception is bound to a name
    assert handler.name is not None, (
        "_time_ago except must bind exception to a name (e.g., `as exc`)"
    )

    # Verify logger.debug is called with the bound exception
    body_src = ast.unparse(handler)
    assert "log.debug" in body_src, (
        "_time_ago except must call log.debug, not silently swallow"
    )

    # Verify the bound name is used in the debug message
    assert handler.name in body_src, (
        f"_time_ago except must use the bound name '{handler.name}' in the log call"
    )


def test_no_bare_time_ago_except():
    """Ensure no bare `except (ValueError, TypeError): pass` anti-pattern."""
    src = _load_source()

    # Check for the anti-pattern: except (...) without 'as' followed by just pass
    lines = src.split("\n")
    in_time_ago = False
    for i, line in enumerate(lines):
        if "def _time_ago" in line:
            in_time_ago = True
        elif in_time_ago and line.strip().startswith("def "):
            in_time_ago = False

        if in_time_ago:
            # Look for bare except pattern
            is_valueerror_typeerror_except = (
                "except (ValueError, TypeError):" in line
                or "except (TypeError, ValueError):" in line
            )
            if is_valueerror_typeerror_except:
                # Check next few lines for just 'pass' or 'return'
                next_lines = "\n".join(lines[i+1:i+3])
                assert "pass" not in next_lines or "log.debug" in next_lines, (
                    "Bare except with only pass found in _time_ago"
                )
