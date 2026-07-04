"""Regression test: surface silent error in crash_reporter._read_telemetry.

The bare ``except (json.JSONDecodeError, OSError): pass`` was replaced with
``log.debug(...)`` so a corrupt ``telemetry.json`` is no longer invisible
to operators. Control flow is unchanged: a corrupt file still falls
back to ``{}`` (user appears undecided) so consent prompts still work.

This test asserts:
  1. Corrupt JSON → returns ``{}`` (control flow preserved).
  2. A ``crash-reporter`` log record at DEBUG is emitted binding the
     exception (silence is surfaced, not swallowed).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bin import crash_reporter  # noqa: E402


@pytest.fixture
def corrupt_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point TELEMETRY_FILE at a temp file containing malformed JSON."""
    telemetry = tmp_path / "telemetry.json"
    telemetry.write_text("{not valid json,,,,")
    monkeypatch.setattr(crash_reporter, "TELEMETRY_FILE", telemetry)
    return telemetry


def test_corrupt_telemetry_falls_back_to_empty(corrupt_telemetry: Path) -> None:
    """Control flow preserved: corrupt file → user appears undecided."""
    result = crash_reporter._read_telemetry()
    assert result == {}


def test_corrupt_telemetry_emits_debug_log(
    corrupt_telemetry: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The swallowed exception is now bound to a DEBUG log record."""
    with caplog.at_level(logging.DEBUG, logger="crash-reporter"):
        result = crash_reporter._read_telemetry()
    assert result == {}
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "expected a DEBUG log record surfacing the parse error"
    msg = debug_records[0].getMessage()
    assert "telemetry" in msg.lower()
    assert str(corrupt_telemetry) in msg or corrupt_telemetry.name in msg
