#!/usr/bin/env python3
"""
Regression test: bin/red_team_wrong_obs_key.py must surface silent errors
via logger.debug at the socket close swallow inside the ``finally`` block
of the WebSocket handshake attempt (line ~88). The handler must bind the
exception to a name and call logger.debug, not bare ``except OSError: pass``.

Pre-fix, a real ``OSError`` raised by ``sock.close()`` (e.g. EBADF after
the peer already half-closed) was discarded with a bare ``pass`` in the
finally clause. After the fix, the exception is bound to
``sock_close_exc`` and emitted as a DEBUG log so an operator reviewing
the red-team run can see why cleanup itself failed (the swallow is still
intentional: socket cleanup must never propagate).

This test verifies:
1. The module compiles without syntax errors.
2. logging is imported and a module-level logger is defined.
3. The ``try_attempt`` finally-block OSError handler binds the exception
   (handler.name is not None) AND calls logger.debug.
4. No ``except OSError: pass`` swallow remains in the module.

Round 369: Surface silent error in bin/red_team_wrong_obs_key.py
socket close in finally cleanup.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/red_team_wrong_obs_key.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/red_team_wrong_obs_key.py must be syntactically valid Python."""
    _load_source()


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(" in src, (
        "module-level logger must be defined via logging.getLogger(...)"
    )


def _all_except_handlers(tree):
    """Return list of (lineno, handler_node) for every ExceptHandler in tree."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            handlers.append((node.lineno, node))
    return handlers


def test_socket_close_oserror_binds_and_logs():
    """The socket close OSError swallow in try_attempt's finally must
    bind the exception to a name and call logger.debug, not bare `pass`."""
    tree = ast.parse(_load_source())
    handlers = _all_except_handlers(tree)
    # Find OSError handlers that are nested under a try inside a finally
    # (the finally close path). The simplest robust check: an OSError
    # handler whose body is NOT a return / assignment to result — it's a
    # cleanup-only swallow.
    cleanup_handlers = []
    for ln, h in handlers:
        type_src = ast.unparse(h.type) if h.type is not None else ""
        if "OSError" not in type_src:
            continue
        # Skip handlers whose body mutates `result` (the live-handler
        # except at L80). The close cleanup handler is the only one whose
        # body is just a log call (no `result[...] = ...`).
        body_src = ast.unparse(h)
        if "result[" in body_src:
            continue
        cleanup_handlers.append((ln, h))
    assert cleanup_handlers, (
        "no OSError cleanup handler found in try_attempt's finally"
    )
    h = cleanup_handlers[0][1]
    assert h.name is not None, (
        "OSError cleanup handler must bind exception to a name "
        "(e.g. `as sock_close_exc`)"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "OSError cleanup handler must call logger.debug, not bare `pass`"
    )
    # Bound name should be referenced in the logger.debug call so the
    # exception text actually flows into the log
    assert h.name in body_src, (
        f"bound exception name {h.name!r} should be referenced in "
        f"the logger.debug call so its type+message reach the log"
    )


def test_no_bare_except_oserror_pass():
    """No `except OSError: pass` may remain in the module — every OSError
    handler must bind the exception to a name."""
    tree = ast.parse(_load_source())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        type_str = ast.unparse(node.type)
        if "OSError" not in type_str:
            continue
        if node.name is None and len(node.body) == 1 and isinstance(
            node.body[0], ast.Pass
        ):
            offenders.append(
                f"line {node.lineno}: bare 'except OSError: pass' is forbidden"
            )
    assert not offenders, (
        "silent error swallow sites still present: " + "; ".join(offenders)
    )
