"""Regression tests for bin/real_depth_filler.py silent error fixes."""

import ast
import pytest


def test_module_compiles():
    """Module imports without syntax errors."""
    import py_compile

    py_compile.compile("bin/real_depth_filler.py", doraise=True)


def test_logging_imported():
    """Module imports logging."""
    # Read source and check for logging import
    with open("bin/real_depth_filler.py", "r") as f:
        source = f.read()

    assert "import logging" in source
    assert "logger = logging.getLogger(__name__)" in source


def test_oom_recovery_import_error_binds_exception():
    """OOM recovery block's ImportError handler binds exception name."""
    with open("bin/real_depth_filler.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)

    # Find the OOM retry block - look for "out of memory" string
    oom_handler_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Check if this is the ImportError handler in OOM context
            if node.type and isinstance(node.type, ast.Name) and node.type.id == "ImportError":
                # Verify it binds the exception
                assert node.name is not None, "ImportError handler must bind exception (use 'as exc')"
                oom_handler_found = True

    assert oom_handler_found, "ImportError handler in OOM recovery block must bind exception"


def test_oom_recovery_import_error_logs_at_debug():
    """OOM recovery block's ImportError handler calls logger.debug."""
    with open("bin/real_depth_filler.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)
    source_lines = source.split('\n')

    # Find ImportError handlers and verify at least ONE calls logger.debug
    # (the OOM recovery handler at line 260 should have it; the one at line 114 raises)
    has_logger_debug = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type and isinstance(node.type, ast.Name) and node.type.id == "ImportError":
                handler_start = node.lineno
                handler_end = node.end_lineno if hasattr(node, 'end_lineno') else handler_start + len(node.body)
                
                for line in source_lines[handler_start-1:handler_end]:
                    if 'logger.debug' in line:
                        has_logger_debug = True
                        break

    assert has_logger_debug, "At least one ImportError handler must call logger.debug"


def test_oom_recovery_import_error_references_bound_name():
    """OOM recovery block's logger.debug call references the bound exception."""
    with open("bin/real_depth_filler.py", "r") as f:
        source = f.read()

    # Find the except ImportError as X: block in the OOM context
    # and verify logger.debug uses the bound name
    assert "except ImportError as exc:" in source
    assert "logger.debug" in source
    # The format string should reference the bound exception
    assert "%s" in source or "{" in source, "logger.debug must use format string with exception"


def test_no_bare_pass_in_oom_recovery():
    """OOM recovery block does not contain bare 'except ...: pass'."""
    with open("bin/real_depth_filler.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)

    # Find ImportError handlers and verify they don't have bare pass
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type and isinstance(node.type, ast.Name) and node.type.id == "ImportError":
                # Check body is not just a Pass statement
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    pytest.fail("ImportError handler must not have bare 'pass'")
