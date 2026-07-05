"""Tests for silent-error surfacing in ``src/oyster_agent_runner/cs2/beamng_telemetry.py``.

Regression checks for two bare ``except Exception:`` blocks that were
previously silent and are now bound + logged at DEBUG:

  1. ``_disconnect()`` swallows ``BeamNGpy`` ``close()`` errors so the
     script always exits cleanly; the exception is now logged at
     DEBUG with the bound ``exc``.
  2. ``_maybe_write_screenshot()`` swallows PIL ``Image.save()`` errors
     so the telemetry loop keeps going; the exception is now logged at
     DEBUG with the bound ``exc`` and identifying context
     (frame_index, screenshots_dir).

Self-review: scope = one source file + one test file, one logical change
(bind both bare except blocks to ``except Exception as exc:`` + log at
DEBUG), no control-flow change (both still ``return`` after swallow),
DEBUG-only (no PII leak — frame_index + path + exception repr).
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

# Make the source module importable (lives under src/oyster_agent_runner/cs2/)
SRC_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "oyster_agent_runner"
    / "cs2"
    / "beamng_telemetry.py"
)
sys.path.insert(0, str(SRC_PATH.parent.parent.parent / "src"))

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("beamng_telemetry", SRC_PATH)
assert spec is not None and spec.loader is not None
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


SRC = SRC_PATH.read_text(encoding="utf-8")
LOGGER_NAME = "beamng_telemetry"  # the logger name used at module load time


def test_no_bare_except_in_disconnect() -> None:
    """``_disconnect()`` must not have a bare ``except Exception:`` block."""
    # Find the function and inspect its body
    bare = re.search(
        r"def\s+_disconnect\b.*?except\s+Exception\s*:\s*(?:#[^\n]*)?\n",
        SRC,
        re.DOTALL,
    )
    assert not bare, (
        "Bare `except Exception:` in _disconnect() still present at offset "
        f"{bare.start() if bare else '?'}"
    )


def test_no_bare_except_in_maybe_write_screenshot() -> None:
    """``_maybe_write_screenshot()`` must not have a bare ``except Exception:`` block."""
    bare = re.search(
        r"def\s+_maybe_write_screenshot\b.*?except\s+Exception\s*:\s*(?:#[^\n]*)?\n",
        SRC,
        re.DOTALL,
    )
    assert not bare, (
        "Bare `except Exception:` in _maybe_write_screenshot() still present at offset "
        f"{bare.start() if bare else '?'}"
    )


def test_module_logger_defined() -> None:
    """Module-level ``_LOG`` (or ``logger``) is defined at module scope."""
    assert hasattr(bt, "_LOG"), "expected module-level `_LOG` logger"
    assert isinstance(bt._LOG, logging.Logger), (
        f"expected logging.Logger, got {type(bt._LOG)}"
    )
    # Logger name follows Python's getLogger(__name__) convention; the
    # exact suffix depends on how the module was loaded, so accept any
    # name ending in the module's filename.
    assert bt._LOG.name.endswith("beamng_telemetry"), (
        f"expected logger name to end with 'beamng_telemetry', got {bt._LOG.name!r}"
    )


def test_disconnect_logs_close_failure_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_disconnect()`` emits a DEBUG log when ``close()`` raises."""

    class FakeBeamNG:
        def close(self) -> None:
            raise RuntimeError("socket already torn down")

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        # Must not raise (best-effort semantics preserved)
        result = bt._disconnect(FakeBeamNG())  # type: ignore[arg-type]

    assert result is None, f"expected None return on swallow, got {result!r}"
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any(
        "BeamNGpy close() failed" in r.getMessage() for r in debug_records
    ), (
        "expected DEBUG log mentioning 'BeamNGpy close() failed'; got "
        f"{[r.getMessage() for r in debug_records]}"
    )
    assert any(
        "socket already torn down" in r.getMessage() for r in debug_records
    ), (
        "expected DEBUG log to include the bound exception text; got "
        f"{[r.getMessage() for r in debug_records]}"
    )


def test_maybe_write_screenshot_logs_save_failure_at_debug(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """``_maybe_write_screenshot()`` emits a DEBUG log when ``Image.save()`` raises."""

    class FakeImage:
        def save(self, _path) -> None:
            raise OSError("disk full")

    sensors = {"camera": {"colour": FakeImage()}}
    screenshots_dir = tmp_path / "frames"

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        # Must not raise (best-effort semantics preserved)
        result = bt._maybe_write_screenshot(sensors, screenshots_dir, 42)  # type: ignore[arg-type]

    assert result is None, f"expected None return on swallow, got {result!r}"
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any(
        "Failed to save BeamNG screenshot" in r.getMessage()
        for r in debug_records
    ), (
        "expected DEBUG log mentioning 'Failed to save BeamNG screenshot'; got "
        f"{[r.getMessage() for r in debug_records]}"
    )
    msg = next(
        (
            r.getMessage()
            for r in debug_records
            if "Failed to save BeamNG screenshot" in r.getMessage()
        ),
        "",
    )
    assert "frame=42" in msg, f"expected frame_index in DEBUG log: {msg!r}"
    assert str(screenshots_dir) in msg, (
        f"expected screenshots_dir in DEBUG log: {msg!r}"
    )
    assert "disk full" in msg, (
        f"expected bound exception text in DEBUG log: {msg!r}"
    )


def test_maybe_write_screenshot_returns_silently_when_no_camera() -> None:
    """``_maybe_write_screenshot()`` returns None when there is no camera data
    (no exception path → no log expected, behavior preserved)."""
    # sensors with no camera key
    result = bt._maybe_write_screenshot({}, Path("/tmp/does-not-matter"), 0)  # type: ignore[arg-type]
    assert result is None


def test_module_compiles() -> None:
    """The source file parses as valid Python AST."""
    import ast

    ast.parse(SRC)
