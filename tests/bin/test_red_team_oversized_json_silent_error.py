#!/usr/bin/env python3
"""
Regression test: bin/red_team_oversized_json.py must surface silent errors
via logger.debug at the temp-dir rmdir swallow inside main()'s finally
block (line ~123). The handler must bind the exception to a name and
call logger.debug, not bare ``except OSError: pass``.

This test verifies:
1. The module compiles without syntax errors.
2. logging is imported and a module-level logger is defined.
3. The OSError handler in main() binds the exception
   (handler.name is not None) AND calls logger.debug.
4. No ``except OSError: pass`` swallow remains in the module.

Round 372: Surface silent error in bin/red_team_oversized_json.py
temp-dir cleanup rmdir in main()'s finally block.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/red_team_oversized_json.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/red_team_oversized_json.py must be syntactically valid Python."""
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


def test_main_oserror_binds_and_logs():
    """The rmdir cleanup except in main() must bind the exception
    and call logger.debug instead of bare `pass`."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "main")
    assert handlers, "main() has no except blocks"
    # Find the OSError handler
    matching = [
        h for ln, h in handlers
        if "OSError" in ast.unparse(h)
    ]
    assert matching, "OSError except block not found in main()"
    h = matching[0]
    assert h.name is not None, (
        "OSError except must bind the exception to a name (e.g. `as rmdir_exc`)"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "OSError except must call logger.debug, not bare `pass`"
    )


def test_no_bare_except_oserror_pass():
    """No `except OSError: pass` may remain anywhere in the module."""
    tree = ast.parse(_load_source())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # Skip the OSError raise arms used in legitimate flow.
        if not ("OSError" in ast.unparse(node.type) if node.type else False):
            continue
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            offenders.append(node.lineno)
    assert not offenders, (
        f"`except OSError: pass` still present at lines: {offenders}"
    )


def test_rmdir_exc_logger_call_invariant():
    """The OSError handler body must reference the bound exception name
    in the logger.debug call so debugging info is preserved."""
    tree = ast.parse(_load_source())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "main":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.ExceptHandler):
                continue
            if not ("OSError" in ast.unparse(child.type) if child.type else False):
                continue
            assert child.name is not None
            # The bound name should appear in the body source.
            body_src = ast.unparse(child)
            assert child.name in body_src, (
                f"bound exception name `{child.name}` not referenced in handler"
            )
