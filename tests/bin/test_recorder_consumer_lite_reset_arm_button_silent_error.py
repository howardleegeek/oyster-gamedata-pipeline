#!/usr/bin/env python3
"""Regression test: recorder_consumer_lite._reset_arm_button() should surface
errors from the button-config reset block instead of silently swallowing
them with a bare `except Exception: pass`.

This test verifies:
1. _reset_arm_button function exists in the module
2. Its single except handler binds the exception to a named variable
3. The except handler calls _trace() with context including the exception
4. The bare `except Exception: pass` pattern is gone
5. Module compiles without syntax errors

Round 336: Surface silent error in _reset_arm_button() — non-fatal button
reset failure (e.g. _arm_btn destroyed in a previous session, Tk error
on color config, attribute access on NoneType) is now logged via _trace
with the exception type + message so the operator sees why the button
didn't visually reset. Control flow preserved: the method still returns
silently and the main loop continues.
"""

import ast
from pathlib import Path


SOURCE_PATH = "bin/recorder_consumer_lite.py"


def _load_source() -> str:
    return Path(SOURCE_PATH).read_text(encoding="utf-8")


def _find_function(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_module_compiles():
    """Source file must be valid Python."""
    compile(_load_source(), SOURCE_PATH, "exec")


def test_reset_arm_button_exists():
    """_reset_arm_button function must exist in the module."""
    tree = ast.parse(_load_source())
    fn = _find_function(tree, "_reset_arm_button")
    assert fn is not None, "_reset_arm_button function must exist"


def test_reset_arm_button_no_bare_except_pass():
    """The try/except in _reset_arm_button must not be a bare 'except Exception: pass'."""
    tree = ast.parse(_load_source())
    fn = _find_function(tree, "_reset_arm_button")
    assert fn is not None, "_reset_arm_button function must exist"

    try_blocks = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert len(try_blocks) >= 1, "_reset_arm_button must contain at least one try block"

    for try_node in try_blocks:
        for handler in try_node.handlers:
            # Bare except (no type)
            if handler.type is None:
                raise AssertionError(
                    "Bare 'except:' is forbidden — must specify Exception "
                    "and bind it to a name"
                )
            # Bare `except Exception:` with no `as`
            if (
                isinstance(handler.type, ast.Name)
                and handler.type.id == "Exception"
                and handler.name is None
            ):
                raise AssertionError(
                    "Bare 'except Exception:' in _reset_arm_button must bind "
                    "the exception (e.g., 'except Exception as e:')"
                )


def test_reset_arm_button_except_binds_exception():
    """The except handler in _reset_arm_button must bind the exception to a name."""
    tree = ast.parse(_load_source())
    fn = _find_function(tree, "_reset_arm_button")
    assert fn is not None, "_reset_arm_button function must exist"

    try_blocks = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert try_blocks, "_reset_arm_button must contain at least one try block"

    # Find at least one handler that binds Exception to a name
    found_bound_handler = False
    for try_node in try_blocks:
        for handler in try_node.handlers:
            if handler.name is not None and handler.name != "_trace":
                # Has an `as <name>` binding
                if (
                    isinstance(handler.type, ast.Name)
                    and handler.type.id == "Exception"
                ):
                    found_bound_handler = True
                    break
        if found_bound_handler:
            break

    assert found_bound_handler, (
        "_reset_arm_button must have an 'except Exception as <name>:' "
        "handler that binds the exception for inspection"
    )


def test_reset_arm_button_except_calls_trace():
    """The except handler body must call _trace() with the bound exception."""
    tree = ast.parse(_load_source())
    fn = _find_function(tree, "_reset_arm_button")
    assert fn is not None, "_reset_arm_button function must exist"

    try_blocks = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert try_blocks, "_reset_arm_button must contain at least one try block"

    # Find a handler that calls _trace(...) with the bound exception in the format string
    found_trace_call = False
    for try_node in try_blocks:
        for handler in try_node.handlers:
            if handler.name is None:
                continue
            bound_name = handler.name
            for sub in ast.walk(handler):
                if not isinstance(sub, ast.Call):
                    continue
                if not isinstance(sub.func, ast.Name):
                    continue
                if sub.func.id != "_trace":
                    continue
                # _trace has at least one positional arg
                if not sub.args:
                    continue
                first_arg = sub.args[0]
                if not isinstance(first_arg, ast.JoinedStr):
                    continue
                # Check that the f-string references the bound exception name
                fstring_uses_bound_name = False
                for value in first_arg.values:
                    if (
                        isinstance(value, ast.FormattedValue)
                        and isinstance(value.value, ast.Name)
                        and value.value.id == bound_name
                    ):
                        fstring_uses_bound_name = True
                        break
                if fstring_uses_bound_name:
                    found_trace_call = True
                    break
            if found_trace_call:
                break
        if found_trace_call:
            break

    assert found_trace_call, (
        "_reset_arm_button's except handler must call _trace(f'...{bound_name}...') "
        "so the operator sees why the button reset failed"
    )
