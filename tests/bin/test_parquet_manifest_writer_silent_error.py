#!/usr/bin/env python3
"""
Regression test: bin/parquet_manifest_writer.py must surface silent errors
via logger.debug at the best-effort cleanup OSError swallow site in
main()'s `finally` block. The except block must bind the exception to a
name and call logger.debug, not swallow the traceback with a bare
`except OSError: pass`.

Cleanup races (parallel unlink, Windows AV scanner, read-only mounts) are
routine and should not fail the function, but they MUST be observable at
DEBUG so postmortem analysis is possible when log level is raised.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The cleanup OSError handler binds the exception to a name
4. The cleanup OSError handler calls logger.debug (not a bare `pass`)
5. No bare `except ...: pass` pattern remains in the module
6. The cleanup block still removes both file and parent dir (control flow
   preserved — unlink then rmdir, not reordered or skipped)

Round 376: Surface silent errors in bin/parquet_manifest_writer.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/parquet_manifest_writer.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/parquet_manifest_writer.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_main_function(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("function main not found")


def test_cleanup_except_binds_exception():
    """main()'s cleanup OSError handler must bind the exception to a name."""
    tree = ast.parse(_load_source())
    main_fn = _find_main_function(tree)
    handlers = [
        child
        for child in ast.walk(main_fn)
        if isinstance(child, ast.ExceptHandler)
    ]
    assert handlers, "main() has no except blocks"
    # Find the OSError handler inside the finally cleanup try-block
    os_handlers = []
    for h in handlers:
        if h.type is None:
            continue
        if "OSError" in ast.unparse(h.type):
            os_handlers.append(h)
    assert os_handlers, "main() has no OSError handler in cleanup path"
    h = os_handlers[0]
    assert h.name is not None, (
        "cleanup OSError handler must bind the exception to a name "
        "(e.g. `except OSError as exc:`)"
    )


def test_cleanup_except_calls_logger_debug():
    """main()'s cleanup OSError handler must call logger.debug, not bare pass."""
    tree = ast.parse(_load_source())
    main_fn = _find_main_function(tree)
    handlers = [
        child
        for child in ast.walk(main_fn)
        if isinstance(child, ast.ExceptHandler)
    ]
    os_handlers = [
        h
        for h in handlers
        if h.type is not None and "OSError" in ast.unparse(h.type)
    ]
    assert os_handlers, "main() has no OSError handler in cleanup path"
    h = os_handlers[0]
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "cleanup OSError handler must call logger.debug, not bare `pass`"
    )
    # Explicitly assert no bare `pass` statement in the handler
    for stmt in h.body:
        assert not isinstance(stmt, ast.Pass), (
            "cleanup OSError handler must not contain a bare `pass` statement; "
            "use `logger.debug(...)` instead"
        )


def test_no_bare_except_pass_anywhere():
    """Module must contain no `except ...: pass` anti-pattern."""
    tree = ast.parse(_load_source())
    bare_pass_lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.body:
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                bare_pass_lines.append(node.lineno)
    assert not bare_pass_lines, (
        f"module has bare `except ...: pass` at line(s) {bare_pass_lines}; "
        f"bind the exception and call logger.debug instead"
    )


def test_cleanup_block_still_removes_file_and_parent():
    """Control flow preserved: the cleanup try-block must still call both
    unlink() and rmdir() on output_path in the same order as before."""
    src = _load_source()
    # Find the cleanup try-block (the one inside main()'s finally)
    tree = ast.parse(src)

    def _is_cleanup_try(node):
        if not isinstance(node, ast.Try):
            return False
        # Must have a finalbody (the finally)
        if not node.finalbody:
            return False
        # Body must contain unlink() and rmdir() on output_path
        body_src = ast.unparse(node)
        return (
            "output_path.unlink" in body_src
            and "output_path.parent.rmdir" in body_src
        )

    cleanup_tries = [n for n in ast.walk(tree) if _is_cleanup_try(n)]
    assert cleanup_tries, "cleanup try-block (unlink + rmdir) not found"
    cleanup = cleanup_tries[0]
    body_src = ast.unparse(cleanup)
    # unlink must appear before rmdir (order preserved)
    unlink_idx = body_src.find("output_path.unlink")
    rmdir_idx = body_src.find("output_path.parent.rmdir")
    assert unlink_idx < rmdir_idx, (
        f"cleanup order must remain unlink-then-rmdir; "
        f"got unlink@{unlink_idx} rmdir@{rmdir_idx}"
    )
    # Both calls still present (no accidental removal)
    assert unlink_idx != -1 and rmdir_idx != -1
