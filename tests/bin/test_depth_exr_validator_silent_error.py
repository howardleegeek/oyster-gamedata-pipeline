"""Regression test: bin/depth_exr_validator.py no longer silently swallows
exceptions in check_magic_byte() and check_structural().

We monkeypatch the open() / OpenEXR.InputFile paths to raise, then assert
that the module logger records a debug message that includes the offending
path and the exception. Control flow is unchanged: both helpers still
return False on error (so an EXR with an unreadable file is still flagged
as invalid, not as a hard crash).

Self-review: scope = one file (bin/depth_exr_validator.py), one logical
change (bind previously-bare except to `_exc` + _LOG.debug).
"""

from __future__ import annotations

import builtins
import logging
import sys
from pathlib import Path

import pytest

# Ensure repo root is importable.
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

import depth_exr_validator as dev  # noqa: E402


# ---------------------------------------------------------------------------
# check_magic_byte
# ---------------------------------------------------------------------------


def test_check_magic_byte_logs_on_oserror(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unreadable file should be logged at DEBUG and return False (no crash)."""
    missing = tmp_path / "does_not_exist.exr"
    with caplog.at_level(logging.DEBUG, logger="depth_exr_validator"):
        result = dev.check_magic_byte(missing)
    assert result is False
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "expected a DEBUG log record on OSError"
    assert any(str(missing) in r.getMessage() for r in debug_records), (
        f"DEBUG log should mention the offending path; got: "
        f"{[r.getMessage() for r in debug_records]}"
    )


def test_check_magic_byte_logs_on_unexpected_exception(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-OSError exception should also be logged, not silently dropped."""
    target = tmp_path / "broken.exr"
    target.write_bytes(b"")  # exists, so open() won't raise

    def _explode(_self, *_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(builtins, "open", _explode)
    with caplog.at_level(logging.DEBUG, logger="depth_exr_validator"):
        result = dev.check_magic_byte(target)
    assert result is False
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "expected a DEBUG log record on RuntimeError"
    assert any("boom" in r.getMessage() for r in debug_records), (
        f"DEBUG log should include the exception message; got: "
        f"{[r.getMessage() for r in debug_records]}"
    )


def test_check_magic_byte_happy_path(tmp_path: Path) -> None:
    """A real EXR-magic-byte file returns True (control flow unchanged)."""
    good = tmp_path / "good.exr"
    good.write_bytes(b"\x76\x2f\x31\x01garbage")
    assert dev.check_magic_byte(good) is True


def test_check_magic_byte_wrong_magic_returns_false(tmp_path: Path) -> None:
    """A non-EXR file returns False without raising (control flow unchanged)."""
    bad = tmp_path / "not_exr.exr"
    bad.write_bytes(b"NOT_EXR")
    assert dev.check_magic_byte(bad) is False


# ---------------------------------------------------------------------------
# check_structural (ImportError short-circuit is preserved)
# ---------------------------------------------------------------------------


def test_check_structural_logs_on_openexr_runtime_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception inside the OpenEXR block should be logged at DEBUG and
    return False, not silently swallowed.
    """
    target = tmp_path / "broken.exr"
    target.write_bytes(b"\x76\x2f\x31\x01")  # passes the magic-byte gate

    # Fake the OpenEXR module so the lazy import succeeds but InputFile blows up.
    class _BoomInputFile:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("openexr boom")

        def __enter__(self):
            raise RuntimeError("openexr boom")

        def __exit__(self, *_args):
            return False

    class _FakeOpenEXR:
        InputFile = _BoomInputFile

    monkeypatch.setitem(sys.modules, "OpenEXR", _FakeOpenEXR)

    with caplog.at_level(logging.DEBUG, logger="depth_exr_validator"):
        result = dev.check_structural(target)
    assert result is False
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "expected a DEBUG log record on OpenEXR RuntimeError"
    assert any("openexr boom" in r.getMessage() for r in debug_records), (
        f"DEBUG log should include the exception message; got: "
        f"{[r.getMessage() for r in debug_records]}"
    )


def test_check_structural_importerror_returns_true(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When OpenEXR is not importable, we still skip (return True) — the
    previous ImportError short-circuit must remain intact.
    """
    target = tmp_path / "ok.exr"
    target.write_bytes(b"")

    # Force the lazy `import OpenEXR` to raise ImportError.
    import builtins as _bi

    real_import = _bi.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "OpenEXR":
            raise ImportError("no OpenEXR installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(_bi, "__import__", _fake_import)
    with caplog.at_level(logging.DEBUG, logger="depth_exr_validator"):
        result = dev.check_structural(target)
    assert result is True, "ImportError path should still skip the check"
    # No debug record expected for the documented ImportError path.
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert not debug_records, (
        f"ImportError is the documented skip-path; should not log; got: "
        f"{[r.getMessage() for r in debug_records]}"
    )
