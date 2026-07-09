"""
Regression tests for silent error swallows in
bin/build_bundled_installer/fetch_fabric.py.

These tests verify that error conditions are logged rather than silently
swallowed. This tick surfaces 4 OSError handlers:
- Line ~186: tmp.unlink() in download retry loop
- Line ~285: dest.unlink() stale cache cleanup
- Line ~302: dest.unlink() after SHA-1 mismatch
- Line ~616: stat().st_size in _dir_size_bytes
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/build_bundled_installer/fetch_fabric.py")


def _load_tree():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src, ast.parse(src)


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _iter_handlers(tree: ast.AST):
    """Yield (handler, function_or_None) for every ExceptHandler in the AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    yield child, node


def _is_oserror_named(handler: ast.ExceptHandler) -> bool:
    """True if this handler is a plain `except OSError as <name>:`."""
    return (
        isinstance(handler.type, ast.Name)
        and handler.type.id == "OSError"
        and handler.name is not None
    )


def test_module_compiles():
    """fetch_fabric.py must be valid Python."""
    _load_tree()


def test_module_has_log_helper():
    """fetch_fabric.py must have a _log helper function."""
    src, _ = _load_tree()
    assert "_log" in src, "_log helper must exist"


def test_all_oserror_handlers_bind_exception():
    """Every plain `except OSError:` in fetch_fabric.py must bind the
    exception to a name (no bare `except OSError: pass`)."""
    _, tree = _load_tree()
    plain_oserror_handlers = [h for h, _ in _iter_handlers(tree) if _is_oserror_named(h)]
    # All 4 sites we care about: tmp.unlink (retry), dest.unlink (stale
    # cache), dest.unlink (SHA mismatch), stat() in _dir_size_bytes.
    assert len(plain_oserror_handlers) >= 4, (
        f"Expected at least 4 plain OSError handlers, found {len(plain_oserror_handlers)}"
    )
    for handler in plain_oserror_handlers:
        assert handler.name is not None, "OSError handler must bind exception to a name"
        # Body must NOT be a single `pass` statement
        body_stmts = [
            s for s in handler.body
            if not (isinstance(s, ast.Pass))
        ]
        # If body has only Pass, that's a silent swallow
        non_pass = [s for s in handler.body if not isinstance(s, ast.Pass)]
        assert non_pass, (
            f"OSError handler bound to {handler.name!r} has only `pass` body — "
            "must call _log() with the bound exception"
        )


def test_no_bare_oserror_pass_anywhere():
    """fetch_fabric.py must not contain any bare `except OSError: pass`."""
    src = SRC_PATH.read_text()
    # Pattern: `except OSError:` (or `except OSError as X:`) followed by a
    # body consisting only of `pass` — but constrained to a few lines so we
    # don't backtrack catastrophically. AST check above covers this in
    # detail; this is a fast lint guard.
    pattern = r"except\s+OSError(?:\s+as\s+\w+)?:\s*\n\s+pass\s*\n"
    matches = re.findall(pattern, src)
    assert not matches, (
        f"Found {len(matches)} bare 'except OSError: pass' statements. "
        "All OSError handlers must bind exception and log context."
    )


def test_dir_size_bytes_handler_binds_and_logs():
    """_dir_size_bytes stat() OSError handler must bind exception and
    call _log with the bound name."""
    _, tree = _load_tree()
    fn = _find_function(tree, "_dir_size_bytes")
    assert fn is not None, "_dir_size_bytes function must exist"

    found_handler = None
    for child in ast.walk(fn):
        if isinstance(child, ast.ExceptHandler) and _is_oserror_named(child):
            found_handler = child
            break
    assert found_handler is not None, (
        "_dir_size_bytes must have an `except OSError as <name>:` handler"
    )
    assert found_handler.name is not None
    # Body must call _log
    body_src = ast.unparse(found_handler)
    assert "_log" in body_src, (
        f"_dir_size_bytes OSError handler must call _log(). "
        f"Got body: {body_src!r}"
    )
    # Bound name must be referenced in the body
    assert found_handler.name in body_src, (
        f"_dir_size_bytes OSError handler must reference bound name "
        f"{found_handler.name!r} in body. Got body: {body_src!r}"
    )


def test_download_retry_tmp_unlink_logs():
    """The tmp.unlink OSError handler must call _log with the bound name."""
    src = SRC_PATH.read_text()
    # Find the `except OSError as tmp_unlink_exc:` block. We anchor on
    # the bound name we know we use, then check the block calls _log.
    assert "except OSError as tmp_unlink_exc:" in src, (
        "tmp.unlink cleanup must bind exception to a name (no bare pass)"
    )
    # Confirm the immediate next line(s) call _log (no bare pass).
    pattern = (
        r"except OSError as tmp_unlink_exc:.*?_log\("
    )
    assert re.search(pattern, src, re.DOTALL), (
        "tmp.unlink OSError handler must call _log() with the bound exception"
    )


def test_sha_mismatch_dest_unlink_logs():
    """The SHA-mismatch dest.unlink OSError handler must call _log with
    the bound name."""
    src = SRC_PATH.read_text()
    assert "except OSError as sha_mismatch_unlink_exc:" in src, (
        "SHA-mismatch dest.unlink must bind exception to a name"
    )
    pattern = r"except OSError as sha_mismatch_unlink_exc:.*?_log\("
    assert re.search(pattern, src, re.DOTALL), (
        "SHA-mismatch dest.unlink OSError handler must call _log()"
    )


def test_stale_cache_dest_unlink_logs():
    """The stale-cache dest.unlink OSError handler must call _log with
    the bound name."""
    src = SRC_PATH.read_text()
    assert "except OSError as stale_cache_exc:" in src, (
        "stale-cache dest.unlink must bind exception to a name"
    )
    pattern = r"except OSError as stale_cache_exc:.*?_log\("
    assert re.search(pattern, src, re.DOTALL), (
        "stale-cache dest.unlink OSError handler must call _log()"
    )


def test_all_bound_names_referenced_in_body():
    """The exception name bound by every plain OSError handler must be
    referenced in the handler body (avoids unused-name lint warning and
    ensures the exception is actually surfaced)."""
    _, tree = _load_tree()
    for handler, _fn in _iter_handlers(tree):
        if not _is_oserror_named(handler):
            continue
        body_src = ast.unparse(handler)
        assert handler.name in body_src, (
            f"OSError handler bound to {handler.name!r} does not reference "
            f"the bound name in its body: {body_src!r}"
        )
