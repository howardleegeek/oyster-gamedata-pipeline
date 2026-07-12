#!/usr/bin/env python3
"""
Regression tests for auto_updater_winsparkle.py silent error handling.

These tests verify that bare `except Exception:` blocks in auto_updater_winsparkle.py
have been fixed to bind the exception and log at DEBUG level.
"""

import ast
from pathlib import Path


def test_no_bare_except_with_exception_binding():
    """Verify all except Exception blocks in auto_updater_winsparkle.py bind the exception."""
    source_path = Path(__file__).parent.parent.parent / "bin" / "auto_updater_winsparkle.py"
    source = source_path.read_text()
    tree = ast.parse(source)

    # Find all except handlers
    bare_except_violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Check if this is `except Exception:` (type is None or Name/Attribute)
            if node.type is None:
                # This is `except:` - already valid Python 3
                bare_except_violations.append(f"Line {node.lineno}: bare `except:` without type")
            elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
                if node.name is None:
                    bare_except_violations.append(
                        f"Line {node.lineno}: `except Exception:` without binding"
                    )

    assert not bare_except_violations, (
        "Found bare except Exception without binding:\n" + "\n".join(bare_except_violations)
    )


def test_logger_imported():
    """Verify logger is imported in auto_updater_winsparkle.py."""
    source_path = Path(__file__).parent.parent.parent / "bin" / "auto_updater_winsparkle.py"
    source = source_path.read_text()

    # Check that logger is imported
    assert "logger = logging.getLogger(__name__)" in source, (
        "logger should be imported and initialized"
    )


def test_fixed_exception_blocks_have_debug_logs():
    """Verify the specific exception handlers that were fixed now have debug logging."""
    source_path = Path(__file__).parent.parent.parent / "bin" / "auto_updater_winsparkle.py"
    source = source_path.read_text()

    # Lines that were fixed in this round
    # Line 109: _emit callback error handler
    # Line 312: _daemon_loop error handler

    # Check that these specific fixes have debug logging
    assert 'except Exception as e:' in source, "No bound exception handlers found"

    # Verify the fixed handlers have debug logs
    # 1. _emit callback error should log
    assert 'Callback error for event=' in source, "Missing log for _emit callback error"

    # 2. _daemon_loop error should log
    assert 'Daemon loop error' in source, "Missing log for daemon loop error"


def test_module_compiles():
    """Verify auto_updater_winsparkle.py compiles without errors."""
    source_path = Path(__file__).parent.parent.parent / "bin" / "auto_updater_winsparkle.py"
    source = source_path.read_text()

    try:
        compile(source, str(source_path), "exec")
    except SyntaxError as e:
        raise AssertionError(f"Module failed to compile: {e}") from e
