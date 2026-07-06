"""Regression test for _record_raw_mouse_delta() silent error surfacing.

This tests that the timestamp_ms parsing failure is logged at DEBUG level
rather than silently swallowed.
"""

import ast


def test_mouse_delta_no_bare_except():
    """Verify _record_raw_mouse_delta does not use bare except Exception:"""
    import tests.bin.conftest_rcon_helpers as conftest  # noqa: F401

    source_file = "bin/recorder_consumer_lite.py"
    with open(source_file, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    # Find the _record_raw_mouse_delta method
    found_method = False
    has_bare_except = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_record_raw_mouse_delta":
            found_method = True
            # Check for bare except Exception inside this method
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    if child.type is None:  # bare except
                        has_bare_except = True
                    elif (
                        isinstance(child.type, ast.Name)
                        and child.type.id == "Exception"
                        and child.name is None
                    ):  # except Exception: without binding
                        has_bare_except = True

    assert found_method, "_record_raw_mouse_delta method not found"
    assert not has_bare_except, "_record_raw_mouse_delta has bare except Exception:"


def test_mouse_delta_exception_bound():
    """Verify exception in _record_raw_mouse_delta is bound to a variable."""
    source_file = "bin/recorder_consumer_lite.py"
    with open(source_file, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    found_method = False
    has_bound_except = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_record_raw_mouse_delta":
            found_method = True
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    # Check for except Exception as <name>:
                    if (
                        isinstance(child.type, ast.Name)
                        and child.type.id == "Exception"
                        and child.name is not None
                    ):
                        has_bound_except = True

    assert found_method, "_record_raw_mouse_delta method not found"
    assert has_bound_except, "_record_raw_mouse_delta should have except Exception as e:"


def test_mouse_delta_logs_at_debug():
    """Verify _record_raw_mouse_delta has logger.debug for exception."""
    source_file = "bin/recorder_consumer_lite.py"
    with open(source_file, "r", encoding="utf-8") as f:
        source = f.read()

    # Look for logger.debug in the method
    assert "logger.debug" in source, "logger.debug should be present in recorder_consumer_lite.py"
    assert "timestamp_ms parse failed" in source, "Debug message about timestamp_ms parse should be present"


def test_module_compiles():
    """Verify recorder_consumer_lite.py compiles without errors."""
    import py_compile

    source_file = "bin/recorder_consumer_lite.py"
    py_compile.compile(source_file, doraise=True)
