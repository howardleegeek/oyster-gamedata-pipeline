#!/usr/bin/env python3
"""
Regression test: bin/recorder_log_analyzer.py must surface silent errors
via logger.debug at the log_size_bytes int-conversion swallow inside
``_parse_log_info`` (line ~235). The handler must bind the exception to
a name and call logger.debug, not bare ``except ValueError: pass``.

This test verifies:
1. The module compiles without syntax errors.
2. logging is imported and a module-level logger is defined.
3. The ``_parse_log_info`` ValueError handler binds the exception
   (handler.name is not None) AND calls logger.debug.
4. No ``except ValueError: pass`` swallow remains in the module.

Round 368: Surface silent error in bin/recorder_log_analyzer.py
log_size_bytes int() conversion inside _parse_log_info.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/recorder_log_analyzer.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/recorder_log_analyzer.py must be syntactically valid Python."""
    _load_source()


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


def test_extract_run_info_valueerror_binds_and_logs():
    """The log_size_bytes int() conversion except in extract_run_info must
    bind the exception and call logger.debug instead of bare `pass`."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "extract_run_info")
    assert handlers, "extract_run_info has no except blocks"
    # Find the ValueError handler
    matching = [
        h for ln, h in handlers
        if "ValueError" in ast.unparse(h)
    ]
    assert matching, "ValueError except block not found in extract_run_info"
    h = matching[0]
    assert h.name is not None, (
        "ValueError except must bind the exception to a name (e.g. `as exc`)"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "ValueError except must call logger.debug, not bare `pass`"
    )


def test_no_bare_except_valueerror_pass():
    """No `except ValueError: pass` may remain anywhere in the module."""
    tree = ast.parse(_load_source())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        type_str = ast.unparse(node.type)
        if "ValueError" not in type_str:
            continue
        # Bare "except ValueError: pass" → handler.name is None AND body is
        # exactly [Pass].
        if node.name is None and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            offenders.append(
                f"line {node.lineno}: bare 'except ValueError: pass' is forbidden"
            )
    assert not offenders, (
        "silent error swallow sites still present: " + "; ".join(offenders)
    )
