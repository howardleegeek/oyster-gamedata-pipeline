"""
Regression test: InputCapture.stop() silent error logging.

Tests that:
1. Module has logger imported
2. stop() method has no bare `except Exception:`
3. Each except block binds the exception and logs at DEBUG level

This test was added after fixing silent error swallowing in the stop() method
of the InputCapture class.
"""

import ast
from pathlib import Path


def test_module_has_logger():
    """Verify recorder_consumer_lite imports and uses a logger."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    assert "import logging" in source or "from logging import" in source
    assert "logger = logging.getLogger(__name__)" in source or "log = logging.getLogger" in source


def test_input_capture_stop_no_bare_except():
    """Verify InputCapture.stop() has no bare `except Exception:` blocks."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)

    # Find the InputCapture class
    input_capture_class = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "InputCapture":
            input_capture_class = node
            break

    assert input_capture_class is not None, "InputCapture class not found"

    # Find the stop method
    stop_method = None
    for item in input_capture_class.body:
        if isinstance(item, ast.FunctionDef) and item.name == "stop":
            stop_method = item
            break

    assert stop_method is not None, "InputCapture.stop method not found"

    # Check for bare except Exception in stop()
    for node in ast.walk(stop_method):
        if isinstance(node, ast.ExceptHandler):
            # Check if it's `except Exception:` (bare, no binding)
            if node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id == "Exception"
            ):
                # Bare except Exception - should have a name binding
                if node.name is None:
                    raise AssertionError(
                        "InputCapture.stop() has bare `except Exception:` at line "
                        f"{node.lineno}. Should bind to a name and log."
                    )


def test_input_capture_stop_binds_exception_and_logs():
    """Verify stop() method binds exception and logs at DEBUG level."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)

    # Find InputCapture.stop method again
    stop_method = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "InputCapture":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "stop":
                    stop_method = item
                    break

    assert stop_method is not None

    # Check each except block in stop() has exception binding + logger.debug call
    found_except_count = 0
    for node in ast.walk(stop_method):
        if isinstance(node, ast.ExceptHandler):
            # Must have a name binding
            assert node.name is not None, (
                f"ExceptHandler at line {node.lineno} has no name binding"
            )
            found_except_count += 1

            # Must have logger.debug call in body
            has_debug_log = False
            for body_node in ast.walk(node):
                if isinstance(body_node, ast.Call):
                    # Check for logger.debug(...)
                    if isinstance(body_node.func, ast.Attribute):
                        if (
                            isinstance(body_node.func.value, ast.Name)
                            and body_node.func.value.id in ("logger", "log")
                            and body_node.func.attr == "debug"
                        ):
                            has_debug_log = True
                            break

            assert has_debug_log, (
                f"ExceptHandler at line {node.lineno} does not call "
                "logger.debug() with the bound exception"
            )

    assert found_except_count >= 2, (
        f"Expected at least 2 except blocks in stop(), found {found_except_count}"
    )


def test_module_compiles():
    """Verify the module compiles without syntax errors."""
    import py_compile

    source_path = Path("bin/recorder_consumer_lite.py")
    try:
        py_compile.compile(str(source_path), doraise=True)
    except py_compile.PyCompileError as e:
        raise AssertionError(f"Module compilation failed: {e}")
