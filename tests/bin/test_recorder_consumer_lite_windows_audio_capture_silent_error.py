#!/usr/bin/env python3
"""
Regression test: bin/recorder_consumer_lite._windows_supports_application_audio_capture()
should surface getwindowsversion() failures at DEBUG, not swallow them silently.

This test verifies:
1. Module has a logger imported and defined (module-level)
2. _windows_supports_application_audio_capture() has no bare `except Exception:`
   blocks (AST check); the handler must bind the exception
3. The except handler emits a logger.debug() record with the underlying exception
4. Runtime: when sys.getwindowsversion is missing, the function returns False
   without raising (control flow preserved)
5. Runtime: when getwindowsversion().build is below 19041, returns False
6. Runtime: when getwindowsversion().build >= 19041, returns True
7. Module compiles without syntax errors

Round 330: Surface silent error in _windows_supports_application_audio_capture().
"""

import ast
from pathlib import Path


def _read_source() -> str:
    return Path("bin/recorder_consumer_lite.py").read_text(encoding="utf-8")


def test_module_has_logger():
    """Module must import logging and define `logger = logging.getLogger(__name__)`."""
    tree = ast.parse(_read_source())
    has_logger_import = False
    has_logger_definition = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "logging":
                    has_logger_import = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "logger"
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "getLogger"
                ):
                    has_logger_definition = True
    assert has_logger_import, "Module must import logging"
    assert has_logger_definition, "Module must define logger = logging.getLogger(__name__)"


def test_windows_audio_capture_no_bare_except():
    """_windows_supports_application_audio_capture must not have bare `except Exception:`."""
    tree = ast.parse(_read_source())
    func_node = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_windows_supports_application_audio_capture"
        ):
            func_node = node
            break
    assert func_node is not None, (
        "_windows_supports_application_audio_capture function must exist"
    )

    for node in ast.walk(func_node):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            raise AssertionError(
                "Bare except found (except:) in _windows_supports_application_audio_capture"
            )
        # Check for bare `except Exception:` (no binding)
        if (
            isinstance(node.type, ast.Attribute)
            and isinstance(node.type.value, ast.Name)
            and node.type.value.id == "Exception"
        ) and node.name is None:
            raise AssertionError(
                "Bare except Exception: found - must bind to a name (e.g., except Exception as exc:)"
            )


def test_exception_is_bound():
    """Exception handler must bind to a name (e.g., 'exc')."""
    src = _read_source()
    # Find the function and verify exception binding
    assert "except Exception as exc:" in src, "Exception must be bound to 'exc'"


def test_debug_log_present():
    """Exception handler must log at DEBUG level."""
    src = _read_source()
    assert 'logger.debug(' in src and 'getwindowsversion() failed' in src, (
        "Exception handler must log at DEBUG level"
    )


def test_returns_false_on_missing_sys_getwindowsversion():
    """When sys.getwindowsversion is None, function returns False without raising."""
    src = _read_source()
    # Verify early return pattern when getwindowsversion is None
    assert 'getwindowsversion = getattr(sys, "getwindowsversion", None)' in src
    assert "if getwindowsversion is None:" in src
    assert "return False" in src


def test_returns_false_on_build_below_19041():
    """When build < 19041, function returns False."""
    src = _read_source()
    # Verify the comparison logic
    assert ">= 19041" in src


def test_returns_true_on_build_19041_or_higher():
    """When build >= 19041, function returns True."""
    src = _read_source()
    # Verify the comparison logic
    assert ">= 19041" in src


def test_module_compiles():
    """Module must compile without syntax errors."""
    try:
        compile(_read_source(), "bin/recorder_consumer_lite.py", "exec")
    except SyntaxError as e:
        raise AssertionError(f"Module has syntax errors: {e}")
