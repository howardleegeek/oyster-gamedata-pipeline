"""Tests for defense_size_limit.py (G084 size-cap defender).

Covers:
  - Happy-path: scan_directory returns results for readable files.
  - FileNotFoundError swallow: file disappears between glob and stat →
    results list omits it, logger emits DEBUG with the path.
  - PermissionError swallow: chmod 000 file → results list omits it,
    logger emits WARNING with the path.
  - Static guard: the original bare ``except (FileNotFoundError,
    PermissionError): continue`` is no longer present in scan_directory.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from oyster_agent_runner.defense_size_limit import (
    SIZE_LIMITS,
    check_file_size,
    scan_directory,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src" / "oyster_agent_runner" / "defense_size_limit.py"


def _make_file(directory: Path, name: str, *, size_bytes: int) -> Path:
    """Create a file of exactly ``size_bytes`` zero-bytes (plus a header)."""
    p = directory / name
    # 1 byte of header, the rest zero-padded to keep tests fast & deterministic.
    with p.open("wb") as fh:
        fh.write(b"\x00" * size_bytes)
    return p


class TestCheckFileSize:
    """Unit tests for check_file_size()."""

    def test_within_action_camera_limit(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, "ok.bin", size_bytes=1024)
        result = check_file_size(f, "action_camera")
        assert result.is_within_limit is True
        assert result.file_size == 1024
        assert result.limit_bytes == SIZE_LIMITS["action_camera"]

    def test_exceeds_action_camera_limit(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, "huge.bin", size_bytes=SIZE_LIMITS["action_camera"] + 1)
        result = check_file_size(f, "action_camera")
        assert result.is_within_limit is False

    def test_unknown_limit_name_raises(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, "x.bin", size_bytes=1)
        with pytest.raises(ValueError, match="Unknown limit"):
            check_file_size(f, "does_not_exist")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        ghost = tmp_path / "ghost.bin"
        with pytest.raises(FileNotFoundError):
            check_file_size(ghost, "action_camera")


class TestScanDirectoryLogging:
    """scan_directory() must surface skipped files via logging, not silence them."""

    def test_happy_path_returns_results(self, tmp_path: Path) -> None:
        _make_file(tmp_path, "a.bin", size_bytes=10)
        _make_file(tmp_path, "b.bin", size_bytes=20)
        results = scan_directory(tmp_path, "action_camera")
        assert len(results) == 2
        assert {r.file_path for r in results} == {
            str(tmp_path / "a.bin"),
            str(tmp_path / "b.bin"),
        }

    def test_vanished_file_logs_debug_and_skips(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Create a real file so glob picks it up, then unlink before scan
        # by monkey-patching check_file_size to raise FileNotFoundError.
        from unittest.mock import patch

        target = _make_file(tmp_path, "ghost.bin", size_bytes=8)

        def boom(path, limit_name):
            raise FileNotFoundError(str(path))

        with caplog.at_level(logging.DEBUG, logger="oyster_agent_runner.defense_size_limit"):
            with patch(
                "oyster_agent_runner.defense_size_limit.check_file_size",
                side_effect=boom,
            ):
                results = scan_directory(tmp_path, "action_camera")
        assert results == []
        debug_records = [
            r for r in caplog.records if r.levelno == logging.DEBUG
        ]
        assert any("vanished file" in r.getMessage() for r in debug_records), (
            f"expected DEBUG 'vanished file' record, got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        assert any(str(target) in r.getMessage() for r in debug_records)

    def test_unreadable_file_logs_warning_and_skips(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Real chmod-000 PermissionError is unreliable across platforms
        # (on macOS the file owner can still stat() mode-0 files, and on
        # Windows chmod is a no-op). Force the failure via patching so
        # the test is deterministic everywhere and exercises the
        # except-PermissionError branch in scan_directory itself.
        from unittest.mock import patch

        target = _make_file(tmp_path, "locked.bin", size_bytes=8)

        def boom(path, limit_name):
            raise PermissionError(13, "Permission denied", str(path))

        with caplog.at_level(
            logging.WARNING,
            logger="oyster_agent_runner.defense_size_limit",
        ):
            with patch(
                "oyster_agent_runner.defense_size_limit.check_file_size",
                side_effect=boom,
            ):
                results = scan_directory(tmp_path, "action_camera")
        assert results == []
        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("unreadable file" in r.getMessage() for r in warnings), (
            f"expected WARNING 'unreadable file' record, got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        assert any(str(target) in r.getMessage() for r in warnings)

    def test_recursive_scan_picks_up_nested_files(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        _make_file(tmp_path, "top.bin", size_bytes=4)
        _make_file(sub, "deep.bin", size_bytes=6)
        results = scan_directory(tmp_path, "action_camera", recursive=True)
        names = {Path(r.file_path).name for r in results}
        assert names == {"top.bin", "deep.bin"}

    def test_non_recursive_scan_ignores_nested_files(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        _make_file(tmp_path, "top.bin", size_bytes=4)
        _make_file(sub, "deep.bin", size_bytes=6)
        results = scan_directory(tmp_path, "action_camera", recursive=False)
        names = {Path(r.file_path).name for r in results}
        assert names == {"top.bin"}


class TestSilentErrorSwallowRemoved:
    """Static guard: the original bare-swallow pattern must be gone."""

    def test_no_bare_pass_in_scan_directory(self) -> None:
        source = _SRC.read_text(encoding="utf-8")
        # Find scan_directory's body
        start = source.find("def scan_directory(")
        assert start != -1, "scan_directory definition not found"
        end = source.find("\ndef ", start + 1)
        body = source[start:end if end != -1 else len(source)]
        assert "except (FileNotFoundError, PermissionError):" not in body, (
            "Bare combined swallow re-introduced; split into FileNotFoundError "
            "(debug) + PermissionError (warning)."
        )
        assert "except FileNotFoundError" in body
        assert "except PermissionError" in body
