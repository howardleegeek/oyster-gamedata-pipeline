"""
Regression tests for silent error swallows in
bin/build_bundled_installer/fetch_minecraft.py.

These tests verify that error conditions are logged rather than silently
swallowed. Round 397 surfaces the 2 OSError handlers in _download_with_retries
(tmp.unlink cleanup on partial download) and _fetch_with_sha1_pin (stale
cache dest.unlink on SHA-1 pin miss).
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/build_bundled_installer/fetch_minecraft.py")


def _load_tree():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src, ast.parse(src)


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_download_with_retries_tmp_unlink_binds_exception():
    """The tmp.unlink OSError handler in _download_with_retries must bind the
    exception to a name (no bare `except OSError: pass`)."""
    _src, tree = _load_tree()
    fn = _find_function(tree, "_download_with_retries")
    assert fn is not None, "_download_with_retries function must exist"
    found = False
    for child in ast.walk(fn):
        if isinstance(child, ast.ExceptHandler) and child.name is not None:
            # Only count the inner tmp.unlink cleanup handler (OSError
            # handler nested inside the download try block).
            if isinstance(child.type, ast.Name) and child.type.id == "OSError":
                found = True
                assert child.name, (
                    "tmp.unlink OSError handler in _download_with_retries "
                    "must bind exception (e.g., 'except OSError as exc:')"
                )
    assert found, (
        "_download_with_retries must have an OSError handler that binds the "
        "exception (for the tmp.unlink cleanup path)"
    )


def test_download_with_retries_tmp_unlink_logs():
    """The tmp.unlink OSError handler in _download_with_retries must call _log
    with the bound exception."""
    src = SRC_PATH.read_text()
    # Match the inner OSError handler block: it should be a non-bare except
    # that calls _log with the bound name embedded in the message.
    pattern = (
        r"except\s+OSError\s+as\s+\w+:[^\n]*\n"
        r"(?:\s+[^\n]*\n)*?"
        r"\s+_log\("
    )
    # Restrict to the first 250 lines of the file (the function lives there).
    head = "\n".join(src.splitlines()[:250])
    match = re.search(pattern, head)
    assert match is not None, (
        "_download_with_retries tmp.unlink OSError handler must call _log() "
        "with the bound exception (no bare pass)"
    )


def test_download_with_retries_no_bare_pass():
    """_download_with_retries must not contain any bare `except OSError: pass`
    on the tmp.unlink cleanup path."""
    src = SRC_PATH.read_text()
    head = "\n".join(src.splitlines()[:250])
    # A bare handler is `except OSError:` (no `as`) followed by a `pass` line
    # with no other code.
    pattern = r"except\s+OSError:\s*\n\s+pass"
    match = re.search(pattern, head)
    assert match is None, (
        "_download_with_retries still has a bare `except OSError: pass` — "
        "the tmp.unlink cleanup must bind the exception and log it"
    )


def test_fetch_with_sha1_pin_stale_unlink_binds_exception():
    """The dest.unlink OSError handler in _fetch_with_sha1_pin (stale cache
    cleanup) must bind the exception to a name."""
    _src, tree = _load_tree()
    fn = _find_function(tree, "_fetch_with_sha1_pin")
    assert fn is not None, "_fetch_with_sha1_pin function must exist"
    found = False
    bound_name: str | None = None
    for child in ast.walk(fn):
        if isinstance(child, ast.ExceptHandler) and child.name is not None:
            if isinstance(child.type, ast.Name) and child.type.id == "OSError":
                found = True
                bound_name = child.name
    assert found, (
        "_fetch_with_sha1_pin must have an OSError handler that binds the "
        "exception (for the stale-cache dest.unlink path)"
    )
    assert bound_name, (
        "_fetch_with_sha1_pin OSError handler must bind exception to a name"
    )


def _fetch_with_sha1_pin_body(src: str) -> str:
    """Extract the body of `_fetch_with_sha1_pin` from raw source.

    The function definition spans multiple lines (multi-line signature with
    trailing `-> int:`), so a simple `def ...:` regex is needed.
    """
    # Use AST to locate the function's end line precisely.
    tree = ast.parse(src)
    fn = _find_function(tree, "_fetch_with_sha1_pin")
    assert fn is not None, "_fetch_with_sha1_pin function must exist"
    lines = src.splitlines()
    # end_lineno is 1-based and points at the last line of the function body.
    return "\n".join(lines[fn.lineno - 1 : fn.end_lineno])


def test_fetch_with_sha1_pin_stale_unlink_logs():
    """The dest.unlink OSError handler in _fetch_with_sha1_pin must call _log
    with the bound exception embedded in the message."""
    src = SRC_PATH.read_text()
    body = _fetch_with_sha1_pin_body(src)
    # The OSError handler should call _log() referencing the bound name.
    pattern = (
        r"except\s+OSError\s+as\s+(?P<name>\w+):[^\n]*\n"
        r"(?:\s+[^\n]*\n)*?"
        r"\s+_log\([^)]*\{\s*(?P=name)\s*\}"
    )
    match = re.search(pattern, body)
    assert match is not None, (
        "_fetch_with_sha1_pin stale-cache OSError handler must call _log() "
        "with the bound exception in the message"
    )


def test_fetch_with_sha1_pin_no_bare_pass():
    """_fetch_with_sha1_pin must not contain a bare `except OSError: pass`
    on the stale-cache dest.unlink path."""
    src = SRC_PATH.read_text()
    body = _fetch_with_sha1_pin_body(src)
    pattern = r"except\s+OSError:\s*\n\s+pass"
    match = re.search(pattern, body)
    assert match is None, (
        "_fetch_with_sha1_pin still has a bare `except OSError: pass` — "
        "the stale-cache dest.unlink must bind the exception and log it"
    )


def test_module_compiles():
    """fetch_minecraft.py must be syntactically valid Python."""
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure


def test_module_has_log_helper():
    """The module must have a `_log` helper used by the surfaced handlers."""
    src = SRC_PATH.read_text()
    assert re.search(r"^def _log\(msg: str\) -> None:", src, re.MULTILINE), (
        "fetch_minecraft.py must define a _log(msg: str) helper for the "
        "surfaced OSError handlers to call"
    )
