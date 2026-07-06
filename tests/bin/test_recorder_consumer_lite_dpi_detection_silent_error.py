"""
Regression tests for silent error swallow in recorder_consumer_lite.py DPI detection.

Previous issue: _minecraft_window_geometry() had a bare `except Exception:` around
GetDpiForWindow call that silently swallowed DPI detection errors.

Fix: Bound exception to `e` and added logger.debug() with hwnd and error context.
"""

import ast
from pathlib import Path

import pytest


def test_module_has_logger():
    """Module must have logger defined."""
    # Read the source file
    source_file = Path("bin/recorder_consumer_lite.py")
    source = source_file.read_text()

    # Parse the AST
    tree = ast.parse(source)

    # Find logger definition
    logger_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    logger_found = True
                    break

    assert logger_found, "logger must be defined in recorder_consumer_lite.py"


def test_minecraft_window_geometry_no_bare_except():
    """_get_minecraft_window_rect function must not have bare except Exception:"""
    source_file = Path("bin/recorder_consumer_lite.py")
    source = source_file.read_text()
    tree = ast.parse(source)

    # Find the function
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_get_minecraft_window_rect":
            # Check for bare except in the function
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    # Bare except: no type specified (or just Exception with no binding)
                    if child.type is None:
                        pytest.fail("Found bare except: in _get_minecraft_window_rect")
                    # Check if it's "except Exception:" without binding
                    if isinstance(child.type, ast.Name) and child.type.id == "Exception":
                        if not child.name:  # No exception binding
                            pytest.fail(
                                "Found bare 'except Exception:' (no exception variable) in _get_minecraft_window_rect"
                            )
            return

    pytest.fail("Could not find _get_minecraft_window_rect function")


def test_minecraft_window_geometry_dpi_except_binds_exception():
    """DPI detection except block must bind the exception to a variable."""
    source_file = Path("bin/recorder_consumer_lite.py")
    source = source_file.read_text()
    tree = ast.parse(source)

    # Find the function and the specific except block for DPI
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_minecraft_window_geometry":
            # Look for the DPI detection try/except block
            source_lines = source.split("\n")
            # Find the line with "GetDpiForWindow"
            for i, line in enumerate(source_lines):
                if "GetDpiForWindow" in line and "try:" in source_lines[i - 1]:
                    # Found the try block, check the except
                    # Find the except block in the next few lines
                    for j in range(i, min(i + 10, len(source_lines))):
                        if "except Exception as" in source_lines[j]:
                            # Check it's properly bound (not bare)
                            if "except Exception:" in source_lines[j]:
                                pytest.fail(
                                    "DPI detection has bare 'except Exception:' without binding"
                                )
                            return

    # If we can't find the specific block, check the function has no bare excepts
    # This is a fallback check
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_minecraft_window_geometry":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    if (
                        isinstance(child.type, ast.Name)
                        and child.type.id == "Exception"
                        and not child.name
                    ):
                        pytest.fail(
                            "Found bare 'except Exception:' (no exception variable) in _minecraft_window_geometry"
                        )
            return


def test_minecraft_window_geometry_dpi_logs_debug():
    """DPI detection except block must log at DEBUG level."""
    source_file = Path("bin/recorder_consumer_lite.py")
    source = source_file.read_text()

    # Check that there's a logger.debug call in the DPI detection except block
    assert "logger.debug" in source, "logger.debug must be present for DPI detection errors"

    # More specifically, check it's in the context of GetDpiForWindow
    # Look for the pattern
    lines = source.split("\n")
    dpi_try_idx = None
    for i, line in enumerate(lines):
        if "GetDpiForWindow" in line and i > 0 and "try:" in lines[i - 1]:
            dpi_try_idx = i
            break

    if dpi_try_idx is not None:
        # Check the next 10 lines for logger.debug
        context = "\n".join(lines[dpi_try_idx : dpi_try_idx + 10])
        assert "logger.debug" in context, (
            "logger.debug must be in the DPI detection except block"
        )


def test_module_compiles():
    """Module must compile without errors."""
    source_file = Path("bin/recorder_consumer_lite.py")
    source = source_file.read_text()
    try:
        compile(source, str(source_file), "exec")
    except SyntaxError as e:
        pytest.fail(f"Syntax error in recorder_consumer_lite.py: {e}")


def test_dpi_detection_error_logs_at_debug(caplog):
    """Verify DPI detection error path logs at DEBUG level."""
    # Verify the DPI detection block specifically has logger.debug with
    # GetDpiForWindow context (not just any except Exception in the file).
    source_file = Path("bin/recorder_consumer_lite.py")
    source = source_file.read_text()
    tree = ast.parse(source)

    # Find _get_minecraft_window_rect function
    target_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_get_minecraft_window_rect":
            target_func = node
            break
    assert target_func is not None, "_get_minecraft_window_rect function not found"

    # Find the except block that mentions GetDpiForWindow
    found_dpi_handler = None
    for child in ast.walk(target_func):
        if isinstance(child, ast.ExceptHandler):
            # Look at the context — find handler whose try-block references GetDpiForWindow
            # Walk up the AST via line numbers: find the matching try
            # Simpler: check if the handler body / preceding source contains GetDpiForWindow
            source_lines = source.split("\n")
            # Look backwards from the handler for the try and GetDpiForWindow
            start = max(0, child.lineno - 15)
            context_before = "\n".join(source_lines[start:child.lineno])
            if "GetDpiForWindow" in context_before:
                found_dpi_handler = child
                break

    assert found_dpi_handler is not None, (
        "Could not find except block protecting GetDpiForWindow call in _get_minecraft_window_rect"
    )

    # Check the handler body contains a logger.debug call referencing GetDpiForWindow
    handler_body = ast.dump(found_dpi_handler)
    assert "GetDpiForWindow" in handler_body, (
        "DPI detection except block must mention GetDpiForWindow in its body"
    )

    # Check that the logger.debug call exists in the handler body
    has_debug_log = False
    for body_node in ast.walk(found_dpi_handler):
        if isinstance(body_node, ast.Call) and isinstance(body_node.func, ast.Attribute):
            if (
                isinstance(body_node.func.value, ast.Name)
                and body_node.func.value.id == "logger"
                and body_node.func.attr == "debug"
            ):
                has_debug_log = True
                break
    assert has_debug_log, "DPI detection except block must call logger.debug()"
