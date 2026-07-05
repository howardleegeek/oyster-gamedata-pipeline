#!/usr/bin/env python3
"""
Regression tests for raw_input_capture.py silent error handling.

These tests verify that bare `except Exception:` blocks in raw_input_capture.py
have been fixed to bind the exception and log at DEBUG level.
"""

import ast
import sys
from pathlib import Path


def test_no_bare_except_with_exception_binding():
    """Verify all except Exception blocks in raw_input_capture.py bind the exception."""
    source_path = Path(__file__).parent.parent.parent / "bin" / "raw_input_capture.py"
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
        f"Found bare except Exception without binding:\n" + "\n".join(bare_except_violations)
    )


def test_logger_imported():
    """Verify logger is imported in raw_input_capture.py."""
    source_path = Path(__file__).parent.parent.parent / "bin" / "raw_input_capture.py"
    source = source_path.read_text()

    # Check that logger is imported
    assert "logger = logging.getLogger(__name__)" in source, (
        "logger should be imported and initialized"
    )


def test_fixed_exception_blocks_have_debug_logs():
    """Verify the specific exception handlers that were fixed now have debug logging."""
    source_path = Path(__file__).parent.parent.parent / "bin" / "raw_input_capture.py"
    source = source_path.read_text()

    # Lines that were fixed in this round - each should now have a debug log nearby
    # Line 235: GetCurrentThreadId
    # Line 256: DefWindowProcW (outer handler)
    # Line 384: on_mouse_delta

    # Check that these specific fixes have debug logging
    assert 'except Exception as e:' in source, "No bound exception handlers found"

    # Verify the fixed handlers have debug logs by checking the modified code
    # 1. GetCurrentThreadId failure should log
    assert 'GetCurrentThreadId failed:' in source, "Missing debug log for GetCurrentThreadId"

    # 2. DefWindowProcW failures should log (both inner and outer)
    assert 'DefWindowProcW failed in wndproc:' in source, "Missing debug log for DefWindowProcW in wndproc"
    assert 'DefWindowProcW failed in outer handler:' in source, "Missing debug log for DefWindowProcW in outer"

    # 3. on_mouse_delta failure should log
    assert 'on_mouse_delta failed:' in source, "Missing debug log for on_mouse_delta"


def test_module_imports_clean():
    """Verify the module imports without errors."""
    source_path = Path(__file__).parent.parent.parent / "bin" / "raw_input_capture.py"

    # Try to compile the module
    try:
        with open(source_path) as f:
            code = compile(f.read(), str(source_path), "exec")
        exec(code, {"__name__": "__main__"})
    except SyntaxError as e:
        raise AssertionError(f"Syntax error in module: {e}")
    except ImportError:
        # Import errors are OK - module has optional dependencies
        pass


if __name__ == "__main__":
    test_no_bare_except_with_exception_binding()
    test_logger_imported()
    test_all_exception_blocks_have_debug_logs()
    test_module_imports_clean()
    print("All tests passed!")
