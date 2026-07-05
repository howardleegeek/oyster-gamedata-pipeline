#!/usr/bin/env python3
"""
Regression test: recorder_consumer_lite._hide_depth_progress_ui() inner _apply()
should surface errors from widget destroy and button re-pack, not swallow them
silently.

This test verifies:
1. Module has a logger imported and defined (module-level)
2. No bare `except Exception:` in _hide_depth_progress_ui (AST check)
3. Both except blocks in the inner _apply() bind the exception
4. Both except blocks log at DEBUG level with context
5. Module compiles without syntax errors

Round 327: Surface silent errors in _hide_depth_progress_ui() inner _apply().
"""

import ast
from pathlib import Path


def test_module_has_logger():
    """Verify module imports and defines a logger."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    has_logger_import = False
    has_logger_definition = False
    for node in ast.walk(tree):
        # Check for: import logging
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "logging":
                    has_logger_import = True
        # Check for: logger = logging.getLogger(...)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    if isinstance(node.value, ast.Call):
                        if isinstance(node.value.func, ast.Attribute):
                            if node.value.func.attr == "getLogger":
                                has_logger_definition = True
    assert has_logger_import, "Module must import logging"
    assert has_logger_definition, "Module must define logger = logging.getLogger(__name__)"


def test_hide_depth_progress_ui_no_bare_except():
    """Verify _hide_depth_progress_ui has no bare except Exception: pass blocks."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_hide_depth_progress_ui":
            func_node = node
            break
    assert func_node is not None, "_hide_depth_progress_ui function must exist"

    # Check both except handlers are bound (have an 'as' clause)
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            # Inner widget destroy / button repack handlers
            if node.type is None:
                raise AssertionError("Bare except: found bare except in _hide_depth_progress_ui")
            # Check that exception is bound to a name
            if (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and node.name is None
            ):
                raise AssertionError(
                    "Bare except: Exception handler must bind exception "
                    "(e.g., 'except Exception as e:')"
                )


def test_widget_destroy_except_logs_debug():
    """Verify widget-destroy except handler binds exception and logs at DEBUG."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    assert 'logger.debug("depth_progress: widget destroy failed: %s", e)' in src, (
        "Widget-destroy except must bind 'e' and log at DEBUG with context"
    )


def test_button_repack_except_logs_debug():
    """Verify button re-pack except handler binds exception and logs at DEBUG."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    assert 'logger.debug("depth_progress: button re-pack failed: %s", e)' in src, (
        "Button re-pack except must bind 'e' and log at DEBUG with context"
    )


def test_module_compiles():
    """Verify the module still compiles after the edits."""
    import py_compile

    py_compile.compile("bin/recorder_consumer_lite.py", doraise=True)
