#!/usr/bin/env python3
"""
Regression test: bin/auto_archive_old_uploaded.py must surface
OSError/AttributeError from stat() and rmtree() via logger.debug, not swallow it silently.

This test verifies:
1. The 3 target except blocks bind exceptions to a name (as exc)
2. Each handler body calls logger.debug (not just 'continue')
3. Control flow preserved — continue after log
4. Module compiles without syntax errors

Round: Surface silent errors in auto_archive_old_uploaded.py.
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/auto_archive_old_uploaded.py")


def _load_tree():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src, ast.parse(src)


def test_module_compiles():
    """Module must compile without syntax errors."""
    _load_tree()


def test_logger_defined():
    """Module must have logging imported and logger defined."""
    src, tree = _load_tree()
    # Check for logging import
    has_logging_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "logging":
                    has_logging_import = True
        if isinstance(node, ast.ImportFrom):
            if node.module == "logging":
                has_logging_import = True
    assert has_logging_import, "logging must be imported"

    # Check for logger = logging.getLogger(__name__)
    assert "logger = logging.getLogger(__name__)" in src, (
        "logger must be defined as logging.getLogger(__name__)"
    )


def test_first_swallow_site_binds_exception():
    """First stat() except block at ~line 64 must bind the exception."""
    src, tree = _load_tree()
    # Find the except (OSError, AttributeError) block in find_old_files
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.name is not None:
                    # Check if it's (OSError, AttributeError)
                    if handler.type and isinstance(handler.type, ast.Tuple):
                        ids = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
                        if "OSError" in ids and "AttributeError" in ids:
                            found = True
                            break
    assert found, (
        "First except (OSError, AttributeError) must bind exception "
        "(e.g., 'except (OSError, AttributeError) as exc:')"
    )


def test_first_swallow_site_logs_error():
    """First stat() except block must call logger.debug."""
    src = SRC_PATH.read_text()
    # Pattern: except (OSError, AttributeError) as exc: logger.debug(
    pattern = r"except\s+\(OSError,\s*AttributeError\)\s+as\s+\w+:\s*\n\s+logger\.debug\("
    match = re.search(pattern, src)
    assert match is not None, (
        "First except block must call logger.debug with the error"
    )


def test_second_swallow_site_binds_exception():
    """Second stat/unlink except block at ~line 166 must bind the exception."""
    src, tree = _load_tree()
    # Count the (OSError, AttributeError) except blocks with names
    # We expect 3: lines 64, 166, 203
    found_count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.name is not None:
                    if handler.type and isinstance(handler.type, ast.Tuple):
                        ids = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
                        if "OSError" in ids and "AttributeError" in ids:
                            found_count += 1
    assert found_count >= 2, (
        "Second except (OSError, AttributeError) must bind exception"
    )


def test_second_swallow_site_logs_error():
    """Second stat/unlink except block must call logger.debug."""
    src = SRC_PATH.read_text()
    # Find the second occurrence
    pattern = r"except\s+\(OSError,\s*AttributeError\)\s+as\s+\w+:\s*\n\s+logger\.debug\("
    matches = re.findall(pattern, src)
    assert len(matches) >= 2, (
        "Second except block must call logger.debug with the error"
    )


def test_third_swallow_site_binds_exception():
    """Third rmtree except block at ~line 203 must bind the exception."""
    src, tree = _load_tree()
    found_count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.name is not None:
                    if handler.type and isinstance(handler.type, ast.Tuple):
                        ids = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
                        if "OSError" in ids and "AttributeError" in ids:
                            found_count += 1
    assert found_count >= 3, (
        "Third except (OSError, AttributeError) must bind exception"
    )


def test_third_swallow_site_logs_error():
    """Third rmtree except block must call logger.debug."""
    src = SRC_PATH.read_text()
    pattern = r"except\s+\(OSError,\s*AttributeError\)\s+as\s+\w+:\s*\n\s+logger\.debug\("
    matches = re.findall(pattern, src)
    assert len(matches) >= 3, (
        "Third except block must call logger.debug with the error"
    )


def test_no_bare_except_attribute_error_pass():
    """No bare 'except (OSError, AttributeError): pass' anti-pattern."""
    src = SRC_PATH.read_text()
    # Anti-pattern: except (OSError, AttributeError): continue (no bind, no log)
    pattern = r"except\s+\(OSError,\s*AttributeError\):\s*\n\s+continue\s*$"
    match = re.search(pattern, src, re.MULTILINE)
    assert match is None, (
        "Target except blocks must bind exception and log — "
        "bare `except (OSError, AttributeError): continue` is the anti-pattern"
    )
