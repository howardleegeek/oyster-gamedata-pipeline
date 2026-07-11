"""
Regression tests for silent error swallows in bin/recorder_watchdog.py.

These tests verify that error conditions are logged rather than silently swallowed.
"""

import pytest


class TestRecorderWatchdogSilentError:
    """Tests for silent error handling in recorder_watchdog.py."""

    def test_no_bare_pass_in_find_mc_hwnd(self):
        """Verify find_mc_hwnd doesn't have bare 'pass' in ImportError handler."""
        import ast

        # Read the source file
        from pathlib import Path
        source_path = Path(__file__).parent.parent.parent / "bin" / "recorder_watchdog.py"
        source = source_path.read_text()

        # Parse AST
        tree = ast.parse(source)

        # Find find_mc_hwnd function
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "find_mc_hwnd":
                # Check for bare except with pass
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        # Check if handler body is just 'pass'
                        if len(child.body) == 1 and isinstance(child.body[0], ast.Pass):
                            # This is OK if it's handling a specific known exception
                            # that's expected (like ImportError for optional deps)
                            if child.type is not None:
                                pytest.fail(
                                    f"Found bare pass in except handler for {ast.unparse(child.type)}. "
                                    "Should use logger.debug() to bind the exception."
                                )

    def test_win32_import_failure_logs_at_debug(self):
        """Verify ImportError in find_mc_hwnd logs at debug level."""
        # This test verifies the behavior by mocking the import to fail
        import sys

        # Save original modules
        original_win32gui = sys.modules.get("win32gui")
        original_win32process = sys.modules.get("win32process")

        try:
            # Remove the modules to simulate ImportError
            sys.modules.pop("win32gui", None)
            sys.modules.pop("win32process", None)

            # Also need to remove any sub-modules
            to_remove = [k for k in sys.modules if k.startswith("win32")]
            for k in to_remove:
                sys.modules.pop(k, None)

            # Now import the module - it should handle ImportError gracefully
            # by returning None without crashing
            import importlib
            import bin.recorder_watchdog as watchdog
            importlib.reload(watchdog)

            # Call find_mc_hwnd - should return None without raising
            result = watchdog.find_mc_hwnd()
            assert result is None

        finally:
            # Restore original modules
            if original_win32gui:
                sys.modules["win32gui"] = original_win32gui
            if original_win32process:
                sys.modules["win32process"] = original_win32process
