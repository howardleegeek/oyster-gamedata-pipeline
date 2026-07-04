"""Regression test for the silent-error swallow in
`bin/pii_redactor.py` `redact_jsonl_file`.

The handler used to be a bare `except json.JSONDecodeError: pass`,
which silently dropped malformed-JSONL-line errors. The fix replaces
the `pass` with a `logger.debug(...)` call that binds the exception
(``exc_info=True``) so the skip is visible in logs (at DEBUG level)
without changing the control flow (the line is still left as-is and
the loop continues).

Checks:
  1. Static guard: the ``redact_jsonl_file`` JSONDecodeError handler
     body must NOT be a bare ``pass``.
  2. Static guard: the same handler body must include ``logger.debug``
     to surface the swallow.
  3. Behavioural guard: calling ``redact_jsonl_file`` on a file that
     contains a malformed JSONL line produces a debug log line and
     the file is still rewritten with the malformed line preserved.
"""

from __future__ import annotations

import ast
import json
import logging
import sys
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

SRC_PATH = BIN_DIR / "pii_redactor.py"


def _find_redact_jsonl_file_handler() -> ast.ExceptHandler:
    """Return the JSONDecodeError handler in redact_jsonl_file."""
    tree = ast.parse(SRC_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "redact_jsonl_file":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    type_text = ast.unparse(child.type) if child.type else "bare"
                    if "JSONDecodeError" in type_text:
                        return child
    pytest.fail(
        "Could not find except json.JSONDecodeError handler in redact_jsonl_file"
    )


def test_no_bare_pass_in_redact_jsonl_file() -> None:
    """redact_jsonl_file JSONDecodeError handler must not be a bare `pass`."""
    handler = _find_redact_jsonl_file_handler()
    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
        pytest.fail(
            "Found bare `pass` in redact_jsonl_file JSONDecodeError handler. "
            "Should use logger.debug(...) to bind the exception."
        )


def test_redact_jsonl_file_handler_logs_via_debug() -> None:
    """redact_jsonl_file JSONDecodeError handler must include logger.debug."""
    handler = _find_redact_jsonl_file_handler()
    handler_src = ast.unparse(handler)
    has_logger_debug = "logger.debug" in handler_src
    has_getlogger_debug = "getLogger" in handler_src and ".debug(" in handler_src
    if not (has_logger_debug or has_getlogger_debug):
        pytest.fail(
            "Found no logger.debug(...) call in redact_jsonl_file "
            "JSONDecodeError handler. Should surface the exception for "
            "debugging."
        )


def test_redact_jsonl_file_logs_malformed_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """redact_jsonl_file logs malformed lines at DEBUG and preserves them."""
    caplog.set_level(logging.DEBUG)
    from pii_redactor import redact_jsonl_file

    target = tmp_path / "session.jsonl"
    malformed = "{this is not valid json\n"
    good = json.dumps({"chat": "hi alice", "user": "alice"}) + "\n"
    target.write_text(malformed + good, encoding="utf-8")

    count = redact_jsonl_file(
        target, player_username="alice", pseudonymized_name="player_deadbeef"
    )

    # The malformed line is left as-is (not counted), the good line is
    # redacted. Either way the file is rewritten.
    assert count == 1
    out = target.read_text(encoding="utf-8").splitlines(keepends=True)
    # Malformed line preserved verbatim.
    assert out[0] == malformed
    # The good line was rewritten as JSON (still parseable).
    assert json.loads(out[1])["chat"] == "[redacted]"
    # A DEBUG log was emitted referencing the skip.
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("malformed" in r.getMessage().lower() for r in debug_records), (
        "Expected a DEBUG log line mentioning malformed JSONL; "
        f"got messages: {[r.getMessage() for r in caplog.records]}"
    )
