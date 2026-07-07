#!/usr/bin/env python3
"""
Regression test: bin/disk_space_manager.py must surface silent errors via
logger.debug at the parse_size() float-conversion swallow site. The
except block must bind the exception to a name and call logger.debug,
not swallow the traceback with a bare `except ValueError: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The parse_size() float-conversion except binds the exception AND
   calls logger.debug (not a bare `pass`)
4. The parse_size() final `int(size_str)` except preserves the cause
   via `raise ... from e` (no swallowed cause)
5. parse_size() still functions correctly for valid input
6. parse_size() still raises ValueError for invalid input

Round 357: Surface silent errors in bin/disk_space_manager.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/disk_space_manager.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/disk_space_manager.py must be syntactically valid Python."""
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


def _build_parents(tree):
    """Return dict mapping child node -> parent node."""
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def test_parse_size_float_except_binds_and_logs():
    """parse_size() float-conversion except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "parse_size")
    assert handlers, "parse_size has no except blocks"
    parents = _build_parents(tree)
    # The float-conversion except is the one inside a For body.
    # Walk up: ExceptHandler -> Try -> For.
    float_handlers = []
    for ln, h in handlers:
        if not (h.type and "ValueError" in ast.unparse(h.type)):
            continue
        # Walk up parent chain looking for a For
        cur = parents.get(h)
        inside_for = False
        while cur is not None:
            if isinstance(cur, ast.For):
                inside_for = True
                break
            cur = parents.get(cur)
        if inside_for:
            float_handlers.append(h)
    assert float_handlers, (
        "parse_size() float-conversion except (inside the for loop) not found"
    )
    h = float_handlers[0]
    assert h.name is not None, (
        "parse_size() float-conversion except must bind exception to a name"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "parse_size() float-conversion except must call logger.debug, not bare `pass`"
    )
    # The original swallow was `except ValueError: pass` — make sure we
    # don't have a bare `pass` statement at the top level of the body.
    pass_stmts = [n for n in h.body if isinstance(n, ast.Pass)]
    assert not pass_stmts, (
        "parse_size() float-conversion except must not contain a bare `pass`"
    )


def test_parse_size_final_except_preserves_cause():
    """parse_size() final `int(size_str)` except must chain the cause via
    `raise ... from e` (not swallow the original ValueError)."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "parse_size")
    parents = _build_parents(tree)
    # Find the except handler that is NOT inside a for loop (the final
    # `int(size_str)` one). Sort by lineno to make the order deterministic.
    outside = []
    for ln, h in handlers:
        cur = parents.get(h)
        inside_for = False
        while cur is not None:
            if isinstance(cur, ast.For):
                inside_for = True
                break
            cur = parents.get(cur)
        if not inside_for:
            outside.append((ln, h))
    assert outside, "parse_size has no except handler outside the for loop"
    _ln, final = sorted(outside)[-1]
    assert final.type and "ValueError" in ast.unparse(final.type)
    assert final.name is not None, "final except must bind exception to a name"
    # Find the Raise statement in the body and verify it has __cause__
    raises = [n for n in final.body if isinstance(n, ast.Raise)]
    assert raises, "final except must contain a raise statement"
    raise_node = raises[0]
    assert raise_node.cause is not None, (
        "final except must use `raise ... from e` to preserve the cause"
    )


def test_parse_size_valid_input():
    """parse_size() must still work for valid input (regression check)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "disk_space_manager", "bin/disk_space_manager.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.parse_size("5GB") == 5 * 1024**3
    assert mod.parse_size("1024MB") == 1024 * 1024**2
    assert mod.parse_size("100B") == 100


def test_parse_size_invalid_input_raises():
    """parse_size() must still raise ValueError for invalid input."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "disk_space_manager", "bin/disk_space_manager.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import pytest
    with pytest.raises(ValueError, match="Invalid size"):
        mod.parse_size("not-a-size")
