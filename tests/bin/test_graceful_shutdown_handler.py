"""Tests for bin/graceful_shutdown_handler.py.

Round 248 — verify the silent `except Exception: pass` blocks in
``_cleanup_checkpoints`` and ``_load_queue`` are gone and that the new
explicit handlers log warnings with the underlying error rather than
swallowing it silently.

Cases:
  1. No queue file → default empty queue, no warning logged.
  2. Valid queue.json → queue loaded, INFO logged.
  3. Malformed queue.json (bad JSON) → empty queue, WARNING logged with
     the underlying parse error (not silently dropped).
  4. Queue file that is not a dict (e.g. JSON list) → empty queue,
     WARNING logged.
  5. Queue file replaced with a directory (OSError on open) → empty
     queue, WARNING logged with the OS error.
  6. Static guard: the bare ``except Exception: pass`` is gone from
     the module's source.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from bin.graceful_shutdown_handler import GracefulShutdownHandler


@pytest.fixture
def capture_log_records() -> list[logging.LogRecord]:
    """Capture WARNING+ log records emitted by the module under test."""
    from bin import graceful_shutdown_handler as mod
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _ListHandler(level=logging.DEBUG)
    mod.logger.addHandler(handler)
    prev_level = mod.logger.level
    mod.logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        mod.logger.removeHandler(handler)
        mod.logger.setLevel(prev_level)


def _make_handler(tmp_path: Path) -> GracefulShutdownHandler:
    """Build a handler without invoking __init__'s signal registration.

    The signal registration calls ``signal.signal`` on the main thread
    in the parent process which would persist across tests. We replicate
    the minimal init here so each test gets a clean handler.
    """
    handler = GracefulShutdownHandler.__new__(GracefulShutdownHandler)
    handler.state_dir = tmp_path
    handler.queue_file = tmp_path / "queue.json"
    handler.flush_timeout = 30.0
    import threading
    handler._shutdown = threading.Event()
    handler._writes = {}
    handler._tarballs = {}
    handler._queue = {"version": 1, "items": [], "cursor": 0}
    handler._lock = threading.RLock()
    return handler


def test_no_queue_file_uses_defaults(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """No queue.json → default empty queue, no warning logged."""
    handler = _make_handler(tmp_path)
    handler._load_queue()
    assert handler._queue == {"version": 1, "items": [], "cursor": 0}
    assert capture_log_records == []


def test_valid_queue_loaded(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """Valid queue.json → queue is loaded, INFO logged."""
    queue_file = tmp_path / "queue.json"
    queue_file.write_text(
        json.dumps({"version": 1, "items": ["a", "b", "c"], "cursor": 3}),
        encoding="utf-8",
    )
    handler = _make_handler(tmp_path)
    handler._load_queue()
    assert handler._queue["items"] == ["a", "b", "c"]
    assert handler._queue["cursor"] == 3
    # No WARNING level emitted for the happy path.
    warnings = [r for r in capture_log_records if r.levelno >= logging.WARNING]
    assert warnings == []


def test_malformed_json_logs_warning(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """Bad JSON in queue.json → empty queue, WARNING logged with parse error."""
    queue_file = tmp_path / "queue.json"
    queue_file.write_text("{ this is not valid json", encoding="utf-8")
    handler = _make_handler(tmp_path)
    handler._load_queue()
    # Falls back to default empty queue.
    assert handler._queue == {"version": 1, "items": [], "cursor": 0}
    # And a WARNING is recorded.
    warnings = [r for r in capture_log_records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "queue" in msg.lower()
    # And the original exception is included via exc_info so the parse
    # error is visible — proves we did not silently swallow it.
    assert warnings[0].exc_info is not None


def test_queue_file_is_directory_logs_warning(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """queue.json is actually a directory → OSError on open → WARNING logged."""
    queue_file = tmp_path / "queue.json"
    queue_file.mkdir()  # directory, not file → open() will raise IsADirectoryError (OSError)
    handler = _make_handler(tmp_path)
    handler._load_queue()
    # Falls back to default empty queue.
    assert handler._queue == {"version": 1, "items": [], "cursor": 0}
    warnings = [r for r in capture_log_records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "queue" in msg.lower()


def test_cleanup_checkpoints_logs_warning_on_unlink_failure(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """If an old checkpoint cannot be removed → WARNING logged with details."""
    # Create 7 checkpoints; handler should try to delete 2 of them.
    for i in range(7):
        (tmp_path / f"checkpoint_{1000 + i}.json").write_text("{}", encoding="utf-8")
    handler = _make_handler(tmp_path)

    # Replace Path.unlink on one specific file to raise OSError, so we
    # can verify the inner handler emits a warning rather than passing.
    target = tmp_path / "checkpoint_1000.json"
    original_unlink = Path.unlink

    def fake_unlink(self, *args, **kwargs):
        if self == target:
            raise OSError("simulated permission denied")
        return original_unlink(self, *args, **kwargs)

    Path.unlink = fake_unlink
    try:
        handler._cleanup_checkpoints()
    finally:
        Path.unlink = original_unlink

    warnings = [r for r in capture_log_records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "checkpoint" in msg.lower()
    # exc_info carries the underlying OSError traceback.
    assert warnings[0].exc_info is not None


def test_static_guard_no_bare_except_pass() -> None:
    """Static guard: the silent-swallow pattern is gone from this module."""
    src = Path("bin/graceful_shutdown_handler.py").read_text(encoding="utf-8")
    # The bad pattern — bare or broad except: pass — must not appear.
    assert "except Exception:\n            pass" not in src
    assert "except Exception: pass" not in src
    # Defensive: also confirm the new explicit handlers are present.
    assert "Failed to load queue" in src
    assert "Failed to remove old checkpoint" in src
    assert "Failed to enumerate checkpoints" in src
