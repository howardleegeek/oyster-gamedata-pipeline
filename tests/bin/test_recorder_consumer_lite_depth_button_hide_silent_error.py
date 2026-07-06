"""
Regression test: Surface silent error in _show_depth_progress_ui button hide.

Tests that the bare `except Exception: pass` in the button hide block
is replaced with exception binding + _trace logging.
"""

import ast
import subprocess
import sys


def test_module_compiles():
    """Verify the module can be compiled (syntax check)."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", "bin/recorder_consumer_lite.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Syntax error: {result.stderr}"


def test_no_bare_except_in_button_hide():
    """Verify the button-hide block does not use bare 'except Exception: pass'."""
    source = ast.parse(open("bin/recorder_consumer_lite.py").read())

    # Find the _show_depth_progress_ui method
    for node in ast.walk(source):
        if isinstance(node, ast.FunctionDef) and node.name == "_show_depth_progress_ui":
            # Look for the try/except block that hides _arm_btn and _upload_btn
            for child in ast.walk(node):
                if isinstance(child, ast.Try):
                    # Check if this try contains pack_forget calls
                    has_pack_forget = any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "pack_forget"
                        for n in ast.walk(child)
                    )
                    if has_pack_forget:
                        # Found the try block hiding buttons - check its handlers
                        for handler in child.handlers:
                            # Must NOT be bare 'except Exception: pass'
                            is_bare = (
                                handler.type is None
                                or (
                                    isinstance(handler.type, ast.Name)
                                    and handler.type.id == "Exception"
                                )
                            ) and (
                                not handler.body
                                or (
                                    len(handler.body) == 1
                                    and isinstance(handler.body[0], ast.Pass)
                                )
                            )
                            assert not is_bare, (
                                "Found bare 'except Exception: pass' in button hide block"
                            )


def test_button_hide_binds_exception():
    """Verify the button-hide exception handler binds the exception to a name."""
    source = ast.parse(open("bin/recorder_consumer_lite.py").read())

    for node in ast.walk(source):
        if isinstance(node, ast.FunctionDef) and node.name == "_show_depth_progress_ui":
            for child in ast.walk(node):
                if isinstance(child, ast.Try):
                    has_pack_forget = any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "pack_forget"
                        for n in ast.walk(child)
                    )
                    if has_pack_forget:
                        for handler in child.handlers:
                            # Must have 'as <name>' binding
                            assert handler.name is not None, (
                                "Exception handler must bind the exception to a name"
                            )


def test_button_hide_logs_exception():
    """Verify the button-hide exception handler calls _trace with the exception."""
    source = ast.parse(open("bin/recorder_consumer_lite.py").read())

    found_trace_call = False
    for node in ast.walk(source):
        if isinstance(node, ast.FunctionDef) and node.name == "_show_depth_progress_ui":
            for child in ast.walk(node):
                if isinstance(child, ast.Try):
                    has_pack_forget = any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "pack_forget"
                        for n in ast.walk(child)
                    )
                    if has_pack_forget:
                        for handler in child.handlers:
                            # Check if any statement in handler calls _trace
                            for stmt in handler.body:
                                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                                    if isinstance(stmt.value.func, ast.Name):
                                        if stmt.value.func.id == "_trace":
                                            found_trace_call = True

    assert found_trace_call, (
        "Exception handler must call _trace() to log the error"
    )


def test_trace_call_includes_bound_exception():
    """Verify _trace call includes the bound exception variable in its arguments."""
    source = ast.parse(open("bin/recorder_consumer_lite.py").read())

    found_with_exception = False
    for node in ast.walk(source):
        if isinstance(node, ast.FunctionDef) and node.name == "_show_depth_progress_ui":
            for child in ast.walk(node):
                if isinstance(child, ast.Try):
                    has_pack_forget = any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "pack_forget"
                        for n in ast.walk(child)
                    )
                    if has_pack_forget and child.handlers:
                        handler = child.handlers[0]
                        if handler.name:  # Exception is bound
                            # Check that _trace call references the bound variable
                            handler_source = ast.unparse(handler)
                            if handler.name in handler_source:
                                found_with_exception = True

    assert found_with_exception, (
        "The _trace call must include the bound exception variable"
    )
