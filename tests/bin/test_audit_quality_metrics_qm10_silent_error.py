"""Tests for `bin/audit_quality_metrics.py` QM10 silent-error-swallow fix.

Three regression checks for the QM10 (check_recording_continuity) swallows
that were previously bare ``except (...): pass`` and are now bound to
``exc`` with a ``logger.debug(...)`` call:

  1. Static guard: no `except (...):\n    pass` may remain in
     check_recording_continuity.
  2. Metadata path: a corrupt metadata.json still returns metadata_duration
     None AND emits a DEBUG log record (instead of being silently dropped).
  3. Frames path: an unreadable frames.jsonl still returns frames_duration
     None AND emits a DEBUG log record.

Self-review: scope = one file (bin/audit_quality_metrics.py), one logical
change (bind previously-bare except to ``exc`` + log.debug in QM10), the
module-level logger was added in the same change.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Add bin to path
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

import audit_quality_metrics as aqm  # noqa: E402


QM10_SRC = (BIN_DIR / "audit_quality_metrics.py").read_text(encoding="utf-8")


def _qm10_body() -> str:
    match = re.search(
        r"def check_recording_continuity\(.*?(?=^def |\Z)",
        QM10_SRC,
        re.M | re.S,
    )
    assert match, "check_recording_continuity not found in source"
    return match.group(0)


def test_no_bare_pass_in_qm10() -> None:
    """No `except (...):\\n    pass` may remain in check_recording_continuity."""
    body = _qm10_body()
    bare_pass = re.search(r"except[^\n]*:\s*\n\s+pass\b", body)
    assert not bare_pass, (
        "Silent-pass still present in check_recording_continuity at offset "
        f"{bare_pass.start() if bare_pass else '?'}: "
        f"{bare_pass.group(0) if bare_pass else ''}"
    )


def test_metadata_parse_failure_logs_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt metadata.json is logged at DEBUG; duration stays None."""
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text("not-valid-json{", encoding="utf-8")
    session = SimpleNamespace(
        metadata_path=str(metadata_path),
        video_path=None,
        frames_jsonl_path=None,
    )
    with caplog.at_level(logging.DEBUG, logger="audit_quality_metrics"):
        result = aqm.check_recording_continuity(session)
    # QM10 should SKIP because fewer than 2 durations are available.
    assert result["id"] == "QM10"
    assert result["status"] == "SKIP"
    assert any(
        "failed to parse metadata duration" in rec.message
        for rec in caplog.records
    ), (
        "expected DEBUG log for metadata parse failure; got "
        f"{[r.message for r in caplog.records]}"
    )


def test_frames_read_failure_logs_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unreadable frames.jsonl is logged at DEBUG; duration stays None."""
    frames_path = tmp_path / "frames.jsonl"
    # Create a file then make it unreadable to trigger an OSError on open().
    frames_path.write_text("a\nb\n", encoding="utf-8")
    frames_path.chmod(0o000)
    try:
        session = SimpleNamespace(
            metadata_path=None,
            video_path=None,
            frames_jsonl_path=str(frames_path),
        )
        with caplog.at_level(logging.DEBUG, logger="audit_quality_metrics"):
            with patch(
                "audit_quality_metrics.open",
                side_effect=PermissionError("denied"),
            ):
                result = aqm.check_recording_continuity(session)
    finally:
        # Restore perms so pytest can clean up tmp_path.
        frames_path.chmod(0o644)
    assert result["id"] == "QM10"
    assert result["status"] == "SKIP"
    assert any(
        "failed to estimate frames duration" in rec.message
        for rec in caplog.records
    ), (
        "expected DEBUG log for frames read failure; got "
        f"{[r.message for r in caplog.records]}"
    )
