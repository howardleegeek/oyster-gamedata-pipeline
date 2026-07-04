"""Regression test for the silent-error swallow in
`bin/continuous_capture_daemon.py` `_load_state`.

The handler used to be `except (json.JSONDecodeError, IOError): pass`,
which silently dropped corrupt-state-file errors. The fix replaces the
`pass` with a `self.logger.debug(...)` call that binds the exception
so the corruption is visible in daemon logs (at DEBUG level) without
changing the control flow (state still defaults to `{}`).

Checks:
  1. Static guard: the `_load_state` JSONDecodeError/IOError handler
     body must NOT be a bare `pass`.
  2. Static guard: the same handler body must include `self.logger.debug`
     (or `logger.debug` via module-level import) to surface the swallow.
  3. Behavioural guard: a constructed daemon with a deliberately
     corrupt state file produces a debug log and the default empty
     state is still returned.
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

SRC_PATH = BIN_DIR / "continuous_capture_daemon.py"


def _find_load_state_handler() -> ast.ExceptHandler:
    """Return the (json.JSONDecodeError, IOError) handler in _load_state."""
    tree = ast.parse(SRC_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_state":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    type_text = ast.unparse(child.type)
                    if "JSONDecodeError" in type_text and "IOError" in type_text:
                        return child
    pytest.fail(
        "Could not find except (json.JSONDecodeError, IOError) handler "
        "in _load_state"
    )


def test_no_bare_pass_in_load_state() -> None:
    """_load_state JSON/IO handler must not be a bare `pass`."""
    handler = _find_load_state_handler()
    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
        pytest.fail(
            "Found bare `pass` in _load_state except handler. Should use "
            "self.logger.debug(...) to bind the exception."
        )


def test_load_state_handler_logs_via_debug() -> None:
    """_load_state JSON/IO handler must include a logger.debug call.

    Acceptable forms:
      - `self.logger.debug(...)`          (after _setup_logging has run)
      - `logger.debug(...)`               (module-level logger reference)
      - `logging.getLogger(...).debug(...)` (works even when called from
        __init__ before _setup_logging binds self.logger)
    """
    handler = _find_load_state_handler()
    handler_src = ast.unparse(handler)
    # Either direct .debug( on a name containing "logger", or a
    # logging.getLogger(...).debug(...) call.
    has_logger_debug = "logger.debug" in handler_src
    has_getlogger_debug = (
        "getLogger" in handler_src and ".debug(" in handler_src
    )
    assert has_logger_debug or has_getlogger_debug, (
        f"Expected a debug-log call in handler body; got:\n{handler_src}"
    )


def test_corrupt_state_file_logs_at_debug(tmp_path, caplog, monkeypatch) -> None:
    """Constructing a daemon with a corrupt state file logs at DEBUG
    and the daemon still defaults to an empty state.

    We monkeypatch the daemon's `state_file` and `heartbeat_log` to a
    temp dir so we don't touch the user's real ~/.oyster/.
    """
    sys.modules.pop("bin.continuous_capture_daemon", None)
    from bin.continuous_capture_daemon import ContinuousCaptureDaemon

    state_file = tmp_path / "daemon_state.json"
    state_file.write_text("{ this is not valid json")
    heartbeat_log = tmp_path / "daemon_heartbeat.log"

    with caplog.at_level(logging.DEBUG, logger="oyster_daemon"):
        daemon = ContinuousCaptureDaemon()
        # Redirect to our tmp paths
        monkeypatch.setattr(daemon, "state_file", state_file)
        monkeypatch.setattr(daemon, "heartbeat_log", heartbeat_log)

        result = daemon._load_state()

    # Control flow: must still default to empty dict.
    assert result == {}, f"Expected default empty state, got: {result!r}"

    # Logging: the corrupt-file swallow must be surfaced.
    debug_msgs = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.DEBUG and r.name == "oyster_daemon"
    ]
    assert any("corrupt daemon state file" in m for m in debug_msgs), (
        f"Expected a debug log mentioning 'corrupt daemon state file', "
        f"got: {debug_msgs}"
    )
