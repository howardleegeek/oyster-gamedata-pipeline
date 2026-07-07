#!/usr/bin/env python3
"""
Regression test: bin/data_precision_audit.py must surface silent errors
via logger at the JSON parse swallow site in load_action_camera.

The except block must bind the exception to a name and call logger,
not swallow the traceback.

This test verifies:
1. The module compiles without syntax errors.
2. A module-level logger is defined.
3. The JSONDecodeError handler in load_action_camera binds the exception
   AND calls logger.
4. No bare `except ...: pass` pattern exists in load_action_camera.

Round: Surface silent errors in bin/data_precision_audit.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/data_precision_audit.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/data_precision_audit.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logger_defined():
    """A module-level logger must be defined."""
    src = _load_source()
    tree = ast.parse(src)
    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    has_logger = True
                    break
    assert has_logger, "module-level logger must be defined (logging.getLogger(__name__))"


def test_load_action_camera_handler_binds_and_logs():
    """The JSONDecodeError handler in load_action_camera must bind
    the exception AND call logger."""
    src = _load_source()
    tree = ast.parse(src)

    # Find the load_action_camera function
    load_action_camera_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "load_action_camera":
            load_action_camera_func = node
            break

    assert load_action_camera_func, "load_action_camera function not found"

    # Find JSONDecodeError handlers in this function
    handlers = []
    for node in ast.walk(load_action_camera_func):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                continue  # bare except
            type_str = ast.unparse(node.type)
            if "JSONDecodeError" in type_str:
                handlers.append((node.lineno, type_str, node))

    assert handlers, "load_action_camera: no JSONDecodeError handler found"

    for lineno, type_str, handler in handlers:
        assert handler.name is not None, (
            f"load_action_camera line {lineno}: `{type_str}` except must bind "
            f"the exception to a name (no silent swallow)"
        )
        body_src = ast.unparse(handler)
        assert "logger." in body_src, (
            f"load_action_camera line {lineno}: `{type_str}` except must call "
            f"logger (debug/info/warning/error), not bare `pass`"
        )


def test_no_bare_pass_in_load_action_camera():
    """load_action_camera must not contain bare 'except: pass' pattern."""
    src = _load_source()
    tree = ast.parse(src)

    # Find the load_action_camera function
    load_action_camera_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "load_action_camera":
            load_action_camera_func = node
            break

    assert load_action_camera_func, "load_action_camera function not found"

    # Check for bare pass in except handlers
    for node in ast.walk(load_action_camera_func):
        if isinstance(node, ast.ExceptHandler):
            # Check if the handler body is just 'pass'
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                # This is a silent swallow - should have been fixed
                assert False, (
                    f"load_action_camera line {node.lineno}: "
                    f"bare 'except: pass' found - must bind and log"
                )
