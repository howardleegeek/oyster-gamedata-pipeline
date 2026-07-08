#!/usr/bin/env python3
"""Regression test: bin/telemetry.py should not silently swallow FileNotFoundError."""

import ast
import pytest


def test_module_compiles():
    """Module should compile without syntax errors."""
    import bin.telemetry  # noqa: F401


def test_logging_and_logger_defined():
    """Module should define logging and use a logger."""
    import bin.telemetry as telemetry

    assert hasattr(telemetry, "logger"), "module should have logger attribute"
    assert telemetry.logger is not None, "logger should be defined"


def test_filenotfound_error_handler_logs():
    """The FileNotFoundError handler at line ~349 should call logger.debug."""
    source_path = "bin/telemetry.py"
    with open(source_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find the main() function
    main_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break

    assert main_func is not None, "main() function should exist"

    # Look for the FileNotFoundError handler in main()
    found_target_handler = False
    for node in ast.walk(main_func):
        if isinstance(node, ast.ExceptHandler):
            # Check if it's catching FileNotFoundError
            if node.type is not None:
                if isinstance(node.type, ast.Name) and node.type.id == "FileNotFoundError":
                    # Check if the body contains logger.debug (not just pass)
                    for stmt in node.body:
                        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                            if hasattr(stmt.value.func, "attr") and stmt.value.func.attr == "debug":
                                found_target_handler = True
                                break

    assert found_target_handler, (
        "FileNotFoundError handler in main() should call logger.debug, not silently pass"
    )


def test_no_bare_filenotfound_pass():
    """There should be no bare 'except FileNotFoundError: pass' in telemetry.py."""
    source_path = "bin/telemetry.py"
    with open(source_path) as f:
        source = f.read()

    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None:
                if isinstance(node.type, ast.Name) and node.type.id == "FileNotFoundError":
                    # Found a FileNotFoundError handler - check if it only has pass
                    if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                        pytest.fail(
                            f"Found bare 'except FileNotFoundError: pass' at line {node.lineno}. "
                            "Should log the error instead."
                        )
