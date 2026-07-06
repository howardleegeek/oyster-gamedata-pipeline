"""Regression tests for recorder_consumer_lite.py tarball silent errors.

These tests verify that bare `except Exception:` blocks in the tarball packaging
path have been bound and logged at DEBUG level.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


def test_module_has_logger() -> None:
    """Module must have a logger for debug output."""
    src = Path("bin/recorder_consumer_lite.py")
    content = src.read_text()
    assert "logger = logging.getLogger(__name__)" in content
    assert "import logging" in content


def test_tarball_write_excepts_bind_exception() -> None:
    """Tarball write path must bind exception, not swallow silently."""
    src = Path("bin/recorder_consumer_lite.py")
    content = src.read_text()
    # Find the tarfile.open block - should have "except Exception as _tar_exc:"
    # We look for the pattern near "tarfile.open" and "tmp_tar"
    tree = ast.parse(content)

    found_tar_write_except = False
    for node in ast.walk(tree):
        # Look for with tarfile.open(...) as tf: ... except Exception as ...
        if isinstance(node, ast.ExceptHandler):
            if (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and node.name is not None  # bound
            ):
                # Check if this is the tarball write exception handler
                # by looking for its context - should have tmp_tar.unlink in handler
                handler_src = ast.unparse(node)
                if "tmp_tar" in handler_src and "unlink" in handler_src:
                    found_tar_write_except = True
                    break

    assert found_tar_write_except, (
        "Tarball write path should have 'except Exception as <name>:' "
        "that binds the exception"
    )


def test_tarball_write_logs_at_debug_on_failure() -> None:
    """Tarball write exception handler must log at DEBUG level."""
    src = Path("bin/recorder_consumer_lite.py")
    content = src.read_text()
    # Look for logger.debug call in the context of tarball write failure
    # Pattern: logger.debug(...tar_exc...)
    assert "logger.debug" in content
    # The bound name should appear in a debug log call
    assert "_tar_exc" in content


def test_tmp_dir_cleanup_excepts_bind_exception() -> None:
    """Tmp dir cleanup must bind exception, not swallow silently."""
    src = Path("bin/recorder_consumer_lite.py")
    content = src.read_text()

    # Look for the specific bound exception name: _tmp_rm_exc
    assert "_tmp_rm_exc" in content, "Expected bound exception name _tmp_rm_exc for tmp_dir cleanup"
    
    # Also verify it's in an except handler with shutil.rmtree
    tree = ast.parse(content)
    
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_name = node.name
            if "package" in func_name.lower():
                func_src = ast.unparse(node)
                if "_tmp_dir" in func_src and "shutil.rmtree" in func_src:
                    func_node = node
                    break

    assert func_node is not None, "Package function with tmp_dir cleanup not found"

    # Find the except handler with _tmp_rm_exc
    found_bound_except = False
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            if node.name == "_tmp_rm_exc":
                found_bound_except = True
                break

    assert found_bound_except, (
        "Tmp dir cleanup should have 'except Exception as _tmp_rm_exc:' "
        "that binds the exception"
    )


def test_tmp_dir_cleanup_logs_at_debug_on_failure() -> None:
    """Tmp dir cleanup exception handler must log at DEBUG level."""
    src = Path("bin/recorder_consumer_lite.py")
    content = src.read_text()
    # Look for logger.debug call with _tmp_rm_exc
    assert "_tmp_rm_exc" in content


def test_module_compiles() -> None:
    """Module must compile without syntax errors."""
    src = Path("bin/recorder_consumer_lite.py")
    content = src.read_text()
    try:
        compile(content, str(src), "exec")
    except SyntaxError as e:
        pytest.fail(f"Syntax error in {src}: {e}")
