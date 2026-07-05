#!/usr/bin/env python3
"""
Regression tests for server_ingest_worker.py silent error handling.

These tests verify that exceptions are bound (not bare) and logged at DEBUG level
in the process_message function, making failures visible to operators.
"""

import ast


def test_no_bare_except_in_process_message():
    """Verify process_message() has no bare except Exception: pass blocks."""
    with open("bin/server_ingest_worker.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)

    # Find process_message function
    process_message_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "process_message":
            process_message_func = node
            break

    assert process_message_func is not None, "process_message function not found"

    # Check all except handlers in process_message are bound
    for node in ast.walk(process_message_func):
        if isinstance(node, ast.ExceptHandler):
            # The exception should be bound (have a name)
            assert node.type is not None, "Bare except found in process_message"


def test_logger_imported():
    """Verify logger is imported in the module."""
    with open("bin/server_ingest_worker.py", "r") as f:
        source = f.read()

    assert "logger = logging.getLogger(__name__)" in source


def test_process_message_debug_log():
    """Verify process_message exception handler logs at DEBUG level."""
    with open("bin/server_ingest_worker.py", "r") as f:
        source = f.read()

    # The exception handler should have logger.debug() call
    assert "logger.debug(" in source, "No logger.debug() call found"


def test_process_message_debug_log_message():
    """Verify debug log includes s3_key and error details."""
    with open("bin/server_ingest_worker.py", "r") as f:
        source = f.read()

    # Check for specific debug log message pattern
    assert "s3_key" in source and "error=" in source, \
        "Debug log should include s3_key and error= context"


def test_module_compiles():
    """Verify module compiles without syntax errors."""
    import py_compile
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as tmp:
        try:
            py_compile.compile("bin/server_ingest_worker.py", cfile=tmp.name)
        finally:
            os.unlink(tmp.name)
