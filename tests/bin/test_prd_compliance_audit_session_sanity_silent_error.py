#!/usr/bin/env python3
"""Regression test: bin/prd_compliance_audit.py audit_group_session_sanity
must surface silent errors via logger at the SS1/SS2/SS3 swallow sites
(ffprobe run, metadata.json read, action_camera.json read, frames.jsonl
read, ac time-span parse).

The except blocks must bind the exception to a name and call logger.debug,
not swallow the traceback with bare `pass`.

This test verifies:
1. The module compiles without syntax errors.
2. Every target except handler in audit_group_session_sanity binds the
   exception AND calls logger.
3. No bare `except ...: pass` pattern exists in audit_group_session_sanity.

Round 370: Surface silent errors in audit_group_session_sanity SS1/SS2/SS3.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/prd_compliance_audit.py")
TARGET_FUNC = "audit_group_session_sanity"

# (lineno_hint, expected_handler_substring) – matched against the type
# string of each ExceptHandler in the target function. We re-derive actual
# linenos dynamically; this list documents intent and is used as a sanity
# floor (at least these many handlers must exist).
_EXPECTED_HANDLER_SUBSTRINGS = (
    "FileNotFoundError",  # ffprobe missing
    "JSONDecodeError",    # metadata.json / action_camera.json parse
    "OSError",            # metadata.json / frames.jsonl OS-level
    "ValueError",         # ffprobe stdout float / ac time span
    "TypeError",          # ac time span
)


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def _find_function(src: str, func_name: str) -> ast.FunctionDef:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    raise AssertionError(f"{func_name} not found in source")


def test_module_compiles():
    """bin/prd_compliance_audit.py must be syntactically valid Python."""
    _load_source()


def test_target_handlers_bind_and_log():
    """Every swallow site in audit_group_session_sanity must bind the
    exception AND call logger.debug (or any logger level)."""
    src = _load_source()
    target = _find_function(src, TARGET_FUNC)
    handlers = []
    for node in ast.walk(target):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                continue  # bare `except:` (legal Python 3)
            handlers.append(node)
    assert handlers, f"{TARGET_FUNC}: no except handlers found"
    for handler in handlers:
        # Bind the exception to a name (no bare `except ...: pass`).
        assert handler.name is not None, (
            f"{TARGET_FUNC} line {handler.lineno}: except handler must bind "
            f"the exception to a name (no silent swallow)"
        )
        body_src = ast.unparse(handler)
        assert "logger." in body_src, (
            f"{TARGET_FUNC} line {handler.lineno}: except body must call "
            f"logger, not bare `pass`"
        )
        # No bare `pass` statement directly under the handler.
        for stmt in handler.body:
            assert not isinstance(stmt, ast.Pass), (
                f"{TARGET_FUNC} line {handler.lineno}: found bare `pass` in "
                f"except body; should call logger"
            )


def test_covers_expected_exception_types():
    """All five expected exception-type families are present in the
    target function's handlers (guards against silent removal)."""
    src = _load_source()
    target = _find_function(src, TARGET_FUNC)
    type_blob = ""
    for node in ast.walk(target):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            type_blob += " " + ast.unparse(node.type)
    for expected in _EXPECTED_HANDLER_SUBSTRINGS:
        assert expected in type_blob, (
            f"{TARGET_FUNC}: expected handler type {expected!r} not found; "
            f"got: {type_blob!r}"
        )


def test_no_bare_except_pass_in_session_sanity():
    """AST scan: no `except X: pass` (where X is not None) inside the
    target function."""
    src = _load_source()
    target = _find_function(src, TARGET_FUNC)
    for node in ast.walk(target):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                raise AssertionError(
                    f"{TARGET_FUNC} line {node.lineno}: bare `except "
                    f"{ast.unparse(node.type)}: pass` is a silent swallow"
                )
