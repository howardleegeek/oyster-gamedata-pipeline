#!/usr/bin/env python3
"""
Regression test: recorder_consumer_lite video layer init functions
(_start_windows_capture_layer and _start_mss_layer) should surface errors
from _wait_for_video_layer_init failure, not swallow them silently.

This test verifies:
1. Module has a logger imported and defined (module-level)
2. Both video init except blocks bind the exception
3. Both except blocks log at DEBUG level with context
4. Module compiles without syntax errors

Round <N>: Surface silent errors in video layer init exception handlers.
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


def test_windows_capture_layer_init_binds_exception():
    """Verify _start_windows_capture_layer binds exception in init failure handler."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    
    # Find _start_windows_capture_layer function
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_start_windows_capture_layer":
            func_node = node
            break
    assert func_node is not None, "_start_windows_capture_layer function must exist"

    # Find the except block that calls _stop_video_capture_handle
    found_handler = False
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            # Check if this is the handler that calls _stop_video_capture_handle
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == "_stop_video_capture_handle":
                    found_handler = True
                    # Verify exception is bound
                    assert node.name is not None, (
                        "Bare except: Exception handler must bind exception "
                        "(e.g., 'except Exception as e:')"
                    )
                    break
    assert found_handler, "Must find except handler calling _stop_video_capture_handle"


def test_mss_layer_init_binds_exception():
    """Verify _start_mss_layer binds exception in init failure handler."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    
    # Find _start_mss_layer function
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_start_mss_layer":
            func_node = node
            break
    assert func_node is not None, "_start_mss_layer function must exist"

    # Find the except block that calls _stop_video_capture_handle
    found_handler = False
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            # Check if this is the handler that calls _stop_video_capture_handle
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == "_stop_video_capture_handle":
                    found_handler = True
                    # Verify exception is bound
                    assert node.name is not None, (
                        "Bare except: Exception handler must bind exception "
                        "(e.g., 'except Exception as e:')"
                    )
                    break
    assert found_handler, "Must find except handler calling _stop_video_capture_handle"


def test_windows_capture_layer_init_logs_debug():
    """Verify _start_windows_capture_layer logs at DEBUG level on init failure."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    
    # Find _start_windows_capture_layer function
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_start_windows_capture_layer":
            func_node = node
            break
    assert func_node is not None, "_start_windows_capture_layer function must exist"

    # Find logger.debug call in the except handler
    found_debug_log = False
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            # Check if this is the handler that calls _stop_video_capture_handle
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == "_stop_video_capture_handle":
                    # Now check for logger.debug in this handler
                    for handler_child in ast.walk(node):
                        if isinstance(handler_child, ast.Call):
                            if isinstance(handler_child.func, ast.Attribute):
                                if handler_child.func.attr == "debug":
                                    found_debug_log = True
                    break
    assert found_debug_log, "Must have logger.debug() in init failure except handler"


def test_mss_layer_init_logs_debug():
    """Verify _start_mss_layer logs at DEBUG level on init failure."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    
    # Find _start_mss_layer function
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_start_mss_layer":
            func_node = node
            break
    assert func_node is not None, "_start_mss_layer function must exist"

    # Find logger.debug call in the except handler
    found_debug_log = False
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            # Check if this is the handler that calls _stop_video_capture_handle
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == "_stop_video_capture_handle":
                    # Now check for logger.debug in this handler
                    for handler_child in ast.walk(node):
                        if isinstance(handler_child, ast.Call):
                            if isinstance(handler_child.func, ast.Attribute):
                                if handler_child.func.attr == "debug":
                                    found_debug_log = True
                    break
    assert found_debug_log, "Must have logger.debug() in init failure except handler"


def test_module_compiles():
    """Verify module compiles without syntax errors."""
    import py_compile
    src_path = Path("bin/recorder_consumer_lite.py")
    compiled = py_compile.compile(str(src_path), doraise=True)
    # Clean up .pyc file
    Path(compiled).unlink(missing_ok=True)
