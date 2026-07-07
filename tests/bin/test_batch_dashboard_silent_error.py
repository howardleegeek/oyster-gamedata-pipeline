#!/usr/bin/env python3
"""
Regression test: bin/batch_dashboard.py must surface silent errors via
logger.debug at the audit_score swallow site in calculate_statistics().
Previously the inner `try/except (ValueError, AttributeError): pass`
silently dropped malformed "achieved/total" score strings, hiding
upstream pipeline data corruption. Now it binds the exception and
logs at DEBUG with the offending score string.

This test verifies:
1. The module compiles without syntax errors
2. The module uses the module-level logger `log`
3. The calculate_statistics's except binds the exception to a name
4. The calculate_statistics's except calls log.debug with the bound exception
5. No bare `except (ValueError, AttributeError): pass` anti-pattern remains
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/batch_dashboard.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/batch_dashboard.py must be syntactically valid Python."""
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
                    if child.type:
                        type_str = ast.unparse(child.type)
                        if any(et in type_str for et in exception_types):
                            return child
    return None


def test_calculate_statistics_except_binds_and_logs():
    """calculate_statistics's except must bind exception and log at DEBUG."""
    src = _load_source()
    tree = ast.parse(src)

    handler = _find_except_handler(
        tree, "calculate_statistics", ["ValueError", "AttributeError"]
    )
    assert handler is not None, (
        "calculate_statistics should have an except handler for ValueError/AttributeError"
    )

    # Verify exception is bound to a name
    assert handler.name is not None, (
        "calculate_statistics except must bind exception to a name (e.g., `as exc`)"
    )

    # Verify logger.debug is called with the bound exception
    body_src = ast.unparse(handler)
    assert "log.debug" in body_src, (
        "calculate_statistics except must call log.debug, not silently swallow"
    )

    # Verify the bound name is used in the debug message
    assert handler.name in body_src, (
        f"calculate_statistics except must use the bound name '{handler.name}' in the log call"
    )


def test_no_bare_calculate_statistics_except():
    """Ensure no bare `except (ValueError, AttributeError): pass` anti-pattern."""
    src = _load_source()

    # Check for the anti-pattern: except (...) without 'as' followed by just pass
    lines = src.split("\n")
    in_func = False
    for i, line in enumerate(lines):
        if "def calculate_statistics" in line:
            in_func = True
        elif in_func and line.strip().startswith("def "):
            in_func = False

        if in_func:
            if (
                "except (ValueError, AttributeError):" in line
                or "except (AttributeError, ValueError):" in line
            ):
                # Check next few lines for just 'pass' or 'return'
                next_lines = "\n".join(lines[i + 1 : i + 3])
                assert "pass" not in next_lines or "log.debug" in next_lines, (
                    "Bare except with only pass found in calculate_statistics"
                )
