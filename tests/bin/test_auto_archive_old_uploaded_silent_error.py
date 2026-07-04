"""Tests for `bin/auto_archive_old_uploaded.py` silent-error-swallow fix.

Four regression checks for the bare ``except (...): pass`` blocks that were
previously swallowing the exception without any logging trace:

  1. Static guard: no `except (...):\n    pass` may remain in the source
     (except inside a docstring / comment).
  2. compress_with_zstd: a failing subprocess call still returns None AND
     emits a DEBUG log record (instead of being silently dropped).
  3. cleanup_old_session_dirs dir_size branch: an unreadable rglob path
     still results in dir_size=0 AND emits a DEBUG log record.
  4. cleanup_old_session_dirs outer: a missing SESSION_DIR still returns
     stats zeroed AND emits a DEBUG log record.

Self-review: scope = one file (bin/auto_archive_old_uploaded.py), one
logical change (bind previously-bare except to ``exc`` + log.debug), the
module-level ``logger = logging.getLogger(__name__)`` already existed.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Add bin to path
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

import auto_archive_old_uploaded as aau  # noqa: E402


SRC = (BIN_DIR / "auto_archive_old_uploaded.py").read_text(encoding="utf-8")


def _strip_strings_and_comments(src: str) -> str:
    """Remove triple-quoted strings and ``#`` comments so the bare-pass
    regex does not match docstring examples."""
    # Drop triple-quoted blocks
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    # Drop trailing line comments
    src = re.sub(r"#[^\n]*", "", src)
    return src


def test_no_bare_pass_in_module() -> None:
    """No `except (...):\\n    pass` may remain in the source."""
    cleaned = _strip_strings_and_comments(SRC)
    bare_pass = re.search(r"except[^\n]*:\s*\n\s+pass\b", cleaned)
    assert not bare_pass, (
        "Silent-pass still present at offset "
        f"{bare_pass.start() if bare_pass else '?'}: "
        f"{bare_pass.group(0) if bare_pass else ''}"
    )


def test_compress_with_zstd_failure_logs_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing zstd subprocess call is logged at DEBUG; return stays None."""
    src_file = tmp_path / "in.bin"
    src_file.write_bytes(b"x" * 32)

    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "simulated zstd failure"

    with patch("auto_archive_old_uploaded.subprocess.run",
               return_value=_FakeResult(), side_effect=OSError("boom")), \
         caplog.at_level(logging.DEBUG, logger="auto_archive_old_uploaded"):
        result = aau.compress_with_zstd(src_file)
    assert result is None
    assert any(
        "zstd compression failed" in rec.message for rec in caplog.records
    ), (
        "expected DEBUG log for zstd failure; got "
        f"{[r.message for r in caplog.records]}"
    )


def test_dir_size_failure_logs_at_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An OSError during size walk is logged at DEBUG; dir_size=0."""
    # Point SESSION_DIR at a temp dir; we'll poison rglob() to raise.
    monkeypatch.setattr(aau, "SESSION_DIR", tmp_path, raising=False)
    target = tmp_path / "clip-fake"
    target.mkdir()
    (target / "data.bin").write_bytes(b"hi")
    uploaded = target / ".uploaded"
    uploaded.write_text("ok")
    # Backdate the .uploaded marker so the cleanup branch is taken.
    old = time.time() - 30 * 86400
    os.utime(uploaded, (old, old))

    real_rglob = Path.rglob

    def _explode_rglob(self, pattern):  # noqa: ANN001
        # Yield one real file then raise to simulate partial-walk failure.
        it = iter(real_rglob(self, pattern))
        try:
            first = next(it)
        except StopIteration:
            raise OSError("simulated rglob failure")
        yield first
        raise OSError("simulated rglob failure")

    with patch.object(Path, "rglob", _explode_rglob), \
         caplog.at_level(logging.DEBUG, logger="auto_archive_old_uploaded"):
        stats = aau.cleanup_old_session_dirs()

    # dir_size calc was swallowed → 0 bytes freed, but the failure was logged.
    assert stats["total_space_freed_gb"] == 0.0
    assert any(
        "Failed to compute size" in rec.message for rec in caplog.records
    ), (
        "expected DEBUG log for rglob failure; got "
        f"{[r.message for r in caplog.records]}"
    )


def test_missing_session_dir_logs_at_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing SESSION_DIR is logged at DEBUG; stats stays zeroed."""
    missing = tmp_path / "does_not_exist"
    monkeypatch.setattr(aau, "SESSION_DIR", missing, raising=False)
    with caplog.at_level(logging.DEBUG, logger="auto_archive_old_uploaded"):
        stats = aau.cleanup_old_session_dirs()
    assert stats["directories_removed"] == 0
    assert stats["total_space_freed_gb"] == 0.0
    assert any(
        "Failed to iterate session dir" in rec.message
        for rec in caplog.records
    ), (
        "expected DEBUG log for missing session dir; got "
        f"{[r.message for r in caplog.records]}"
    )


def test_module_imports_clean() -> None:
    """Defensive: re-importing the module must not raise or exit."""
    # Reload to confirm side-effect-free import path.
    importlib.reload(aau)
    assert aau.logger is not None
