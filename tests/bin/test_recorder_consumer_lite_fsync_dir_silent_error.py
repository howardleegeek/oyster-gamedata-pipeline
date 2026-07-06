"""Regression test: _fsync_dir should surface OSError, not swallow silently."""

import ast
from pathlib import Path


def test_fsync_dir_binds_exception_in_open():
    """OSError in os.open should be bound to a name and logged."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)

    # Find _fsync_dir function
    fsync_dir = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_fsync_dir":
            fsync_dir = node
            break

    assert fsync_dir is not None, "_fsync_dir function not found"

    # Find the os.open try block
    open_handler = None
    for stmt in fsync_dir.body:
        if isinstance(stmt, ast.Try):
            for handler in stmt.handlers:
                if handler.type and isinstance(handler.type, ast.Name):
                    if handler.type.id == "OSError":
                        open_handler = handler
                        break

    assert open_handler is not None, "OSError handler for os.open not found"
    # Verify exception is bound to a name (not bare except)
    assert open_handler.name is not None, "OSError should be bound to a name (e.g., 'exc')"


def test_fsync_dir_binds_exception_in_fsync():
    """OSError in os.fsync should be bound to a name and logged."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)

    # Find _fsync_dir function
    fsync_dir = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_fsync_dir":
            fsync_dir = node
            break

    assert fsync_dir is not None

    # Find the os.fsync try block (second try in the function)
    try_blocks = [stmt for stmt in fsync_dir.body if isinstance(stmt, ast.Try)]
    assert len(try_blocks) >= 2, "Expected at least 2 try blocks in _fsync_dir"

    # Second try is os.fsync
    fsync_try = try_blocks[1]
    assert len(fsync_try.handlers) == 1
    handler = fsync_try.handlers[0]
    assert handler.name is not None, "OSError should be bound to a name"


def test_fsync_dir_binds_exception_in_close():
    """OSError in os.close should be bound to a name and logged."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(source)

    # Find _fsync_dir function
    fsync_dir = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_fsync_dir":
            fsync_dir = node
            break

    assert fsync_dir is not None

    # The structure is:
    # try:
    #     fd = os.open(...)
    # except OSError:
    #     return
    # try:
    #     os.fsync(fd)
    # except OSError:
    #     pass
    # finally:
    #     try:
    #         os.close(fd)
    #     except OSError:
    #         pass

    # We need to find the Try node that contains the finally block
    outer_try = None
    for stmt in fsync_dir.body:
        if isinstance(stmt, ast.Try) and stmt.finalbody:
            outer_try = stmt
            break

    assert outer_try is not None, "Outer try with finally block not found"
    assert outer_try.finalbody is not None, "Finally block not found"

    # Inside finally, there's another Try with os.close
    finally_body = outer_try.finalbody[0]
    assert isinstance(finally_body, ast.Try), "Finally should contain a Try"
    assert len(finally_body.handlers) == 1
    handler = finally_body.handlers[0]
    assert handler.name is not None, "OSError in close should be bound to a name"


def test_fsync_dir_calls_trace_on_errors():
    """_fsync_dir should call _trace when OSError occurs."""
    source = Path("bin/recorder_consumer_lite.py").read_text()

    # Check that _trace calls are present with the exception info
    assert "_fsync_dir: os.open failed:" in source
    assert "_fsync_dir: os.fsync failed:" in source
    assert "_fsync_dir: os.close failed:" in source


def test_module_compiles():
    """Module should compile without syntax errors."""
    source = Path("bin/recorder_consumer_lite.py").read_text()
    try:
        compile(source, "bin/recorder_consumer_lite.py", "exec")
    except SyntaxError as e:
        raise AssertionError(f"Syntax error: {e}")
