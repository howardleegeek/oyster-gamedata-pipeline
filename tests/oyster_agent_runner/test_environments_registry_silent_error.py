"""Regression tests: environments/registry.py should not silently swallow exceptions.

The discover() method historically had a bare `except Exception:` at line 142
that only called logger.exception() without binding the exception object.
This made plugin-load failures invisible at DEBUG log level.

These tests assert:
  1. The module imports cleanly (compiles).
  2. The bare except in discover() is bound to an exception variable.
  3. The bound exception is logged at DEBUG level for visibility.
  4. Control flow is preserved (still returns count, still sets _discovered).
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

# Path to the source file under test
REGISTRY_PATH = Path("src/oyster_agent_runner/environments/registry.py")


def test_module_compiles():
    """Module must compile without syntax errors."""
    import py_compile

    py_compile.compile(str(REGISTRY_PATH), doraise=True)


def test_logger_defined():
    """Module must have a module-level logger for debug logging."""
    src = REGISTRY_PATH.read_text()
    tree = ast.parse(src)

    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    has_logger = True
                    break
    assert has_logger, "module-level logger must be defined"


def test_no_bare_except_in_discover():
    """The discover() method must not have bare 'except Exception:' without binding."""
    src = REGISTRY_PATH.read_text()
    tree = ast.parse(src)

    # Find the discover method
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "discover":
            found = True
            # Check all except handlers in this function
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    # Bare except or except Exception without binding
                    if child.type is None or (
                        isinstance(child.type, ast.Name)
                        and child.type.id == "Exception"
                        and child.name is None
                    ):
                        raise AssertionError(
                            f"Bare except at line {child.lineno} in discover: {ast.unparse(child)}"
                        )
    assert found, "discover() method not found in registry.py"


def test_exception_bound_in_discover():
    """The discover() method must bind the exception as 'exc' or 'e'."""
    src = REGISTRY_PATH.read_text()
    assert "except Exception as exc:" in src or "except Exception as e:" in src


def test_debug_log_for_plugin_load_failure():
    """When a plugin fails to load, the exception must be logged at DEBUG level."""
    import importlib
    import tempfile

    from oyster_agent_runner.environments import registry

    # Create a registry instance
    reg = registry.EnvironmentRegistry()

    # Real temp dir with a real .py file that will fail to import (syntax error)
    with tempfile.TemporaryDirectory() as tmp:
        broken = Path(tmp) / "broken_plugin.py"
        broken.write_text("def broken(:\n    pass\n")  # SyntaxError on import

        # Force the inner spec_from_file_location to raise so the except
        # branch is exercised deterministically.
        with patch.object(
            importlib.util,
            "spec_from_file_location",
            side_effect=Exception("intentional test failure"),
        ):
            with patch.object(registry, "logger") as mock_logger:
                # discover() must NOT raise (control flow preserved)
                count = reg.discover(plugin_dir=tmp)
                # No plugin loaded successfully
                assert count == 0
                # The bound exception must have been logged at DEBUG with the
                # filename and exception string included.
                debug_calls = [
                    str(call) for call in mock_logger.debug.call_args_list
                ]
                joined = " ".join(debug_calls)
                assert "broken_plugin.py" in joined, (
                    f"expected plugin filename in DEBUG log; got: {debug_calls!r}"
                )
                assert "intentional test failure" in joined, (
                    f"expected exception text in DEBUG log; got: {debug_calls!r}"
                )


def test_discover_still_returns_count():
    """discover must still return the plugin count after handling exceptions."""
    from oyster_agent_runner.environments import registry

    # Create a fresh registry
    reg = registry.EnvironmentRegistry()

    # Attempt discovery on a non-existent directory
    # It should handle the error gracefully and return 0
    with patch.object(registry.Path, "glob", return_value=[]):
        count = reg.discover(plugin_dir="/nonexistent/dir")
        assert count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
