"""Regression test: bin/recording_watchdog.py must surface silent errors
via logger.debug at the OSError swallow site in count_lines_fast().

Bare `except OSError: return 0` previously masked read errors (e.g.
permission denied, IO errors mid-poll) so the watchdog would silently
treat a 0-line file as a healthy stalled state — the opposite of what
the sidecar is supposed to do. We bind the exception to a name and call
logger.debug so the failure is observable in the watchdog log without
changing the caller's return contract (still returns 0 on failure).

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The OSError handler in count_lines_fast binds the exception AND
   calls logger.debug
4. None of the swallow sites is a bare `except OSError: ...` without
   binding the exception

Round 370: Surface silent error in bin/recording_watchdog.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/recording_watchdog.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/recording_watchdog.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_except_in_func(tree, func_name):
    """Return list of (lineno, handler_node) for ExceptHandlers inside func."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    handlers.append((child.lineno, child))
    return handlers


def test_count_lines_fast_oserror_binds_and_logs():
    """count_lines_fast's OSError handler must bind the exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "count_lines_fast")
    # Pick the except that catches OSError (the read-failure swallow).
    oserror = [h for ln, h in handlers
               if h.type is not None
               and "OSError" in ast.unparse(h.type)]
    assert oserror, "count_lines_fast OSError handler not found"
    h = oserror[0]
    assert h.name is not None, (
        "OSError handler must bind exception to a name (e.g. `except OSError as exc:`)"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "OSError handler must call logger.debug, not bare `return 0`"
    )


def test_no_bare_oserror_pass():
    """No swallow site in the module should be a bare `except OSError:` with no
    bound name and no logger call (the original silent-swallow pattern)."""
    src = _load_source()
    tree = ast.parse(src)
    bare_swallows = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.name is None and node.type is not None:
                type_src = ast.unparse(node.type)
                if "OSError" in type_src:
                    body_src = ast.unparse(node)
                    if "logger" not in body_src:
                        bare_swallows.append((node.lineno, type_src, body_src))
    assert not bare_swallows, (
        f"Found bare `except {bare_swallows[0][1]}` with no logger call "
        f"at line {bare_swallows[0][0]}: {bare_swallows[0][2]}"
    )
