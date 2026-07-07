#!/usr/bin/env python3
"""
Regression test: bin/oyster_monitor.py must surface silent errors via
log.debug at the 2 swallow sites (upload_backlog directory getsize on
unreadable file, ErrorRateChecker.check outer log-file open). Each except
block must bind the exception to a name and call log.debug, not swallow
the traceback with a bare `except X: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger (`log`) is defined
3. The upload_backlog_checker.check() OSError except binds the exception
   AND calls log.debug
4. The ErrorRateChecker.check() outer log open except binds the exception
   AND calls log.debug
5. None of the swallow sites (the 2 specific ones) is a bare `except X: pass`

Round 355: Surface silent errors in bin/oyster_monitor.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/oyster_monitor.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/oyster_monitor.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger named `log`."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert 'log = logging.getLogger("oyster_monitor")' in src, (
        'module-level logger must be defined as '
        '`log = logging.getLogger("oyster_monitor")`'
    )


def _find_except_in_method(tree, class_name, method_name):
    """Return list of (lineno, handler_node) for ExceptHandlers inside the method."""
    handlers = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ClassDef)
            and node.name == class_name
        ):
            for child in node.body:
                if (
                    isinstance(child, ast.FunctionDef)
                    and child.name == method_name
                ):
                    for grandchild in ast.walk(child):
                        if isinstance(grandchild, ast.ExceptHandler):
                            handlers.append((grandchild.lineno, grandchild))
    return handlers


def test_upload_backlog_getsize_except_binds_and_logs():
    """UploadBacklogChecker.check's OSError except must bind + log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_method(tree, "UploadBacklogChecker", "check")
    assert handlers, "UploadBacklogChecker.check has no except blocks"
    # The getsize OSError except is the only one in this method
    # (the except catches OSError when calling os.path.getsize)
    ln, h = handlers[0]  # handlers is list of (lineno, handler_node) tuples
    assert h.name is not None, "OSError except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "log.debug" in body_src, (
        "OSError except must call log.debug, not bare `pass`"
    )


def test_error_rate_outer_log_open_except_binds_and_logs():
    """ErrorRateChecker.check's outer log-file open except must bind + log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_method(tree, "ErrorRateChecker", "check")
    assert handlers, "ErrorRateChecker.check has no except blocks"
    # The outer except handles (OSError, IOError) at the file-open level (line ~225).
    # The inner except at line ~220 handles (ValueError, OSError) inside the loop.
    # We need the outer one: it has a higher line number than the inner one.
    outer_handlers = [
        (ln, h) for ln, h in handlers
        if ln > 220  # outer file-open except is at line 225, inner is at 220
    ]
    assert outer_handlers, "outer (OSError, IOError) except block not found at line > 220"
    ln, h = outer_handlers[0]
    assert h.name is not None, f"Line {ln}: (OSError, IOError) except must bind exception"
    body_src = ast.unparse(h)
    assert "log.debug" in body_src, f"Line {ln}: (OSError, IOError) except must call log.debug"


def test_no_bare_except_pass_silent_swallows():
    """The 2 swallow sites must NOT be bare `except X: pass` pattern.

    Specifically checks:
    - UploadBacklogChecker.check's OSError except binds + logs
    - ErrorRateChecker.check's outer (OSError, IOError) except binds + logs
    """
    tree = ast.parse(_load_source())

    # Check UploadBacklogChecker.check's OSError except
    upload_handlers = _find_except_in_method(tree, "UploadBacklogChecker", "check")
    for ln, h in upload_handlers:
        assert h.name is not None, f"Line {ln}: UploadBacklogChecker except must bind exception"
        body_src = ast.unparse(h)
        assert "log.debug" in body_src, f"Line {ln}: except must call log.debug"

    # Check ErrorRateChecker.check's outer (OSError, IOError) except (not the inner one at line ~220)
    error_handlers = _find_except_in_method(tree, "ErrorRateChecker", "check")
    outer_handlers = [(ln, h) for ln, h in error_handlers if ln > 220]
    assert outer_handlers, "ErrorRateChecker outer (OSError, IOError) except not found at line > 220"
    for ln, h in outer_handlers:
        assert h.name is not None, f"Line {ln}: ErrorRateChecker except must bind exception"
        body_src = ast.unparse(h)
        assert "log.debug" in body_src, f"Line {ln}: except must call log.debug"
