#!/usr/bin/env python3
"""Tests for bin/recorder_log_rotator.py — G277 / F4 recorder log rotator.

Covers:
- _size_bytes (returns int, 0 for missing file, swallows OSError to 0)
- rotate (cascades .N → .N+1, drops oldest at .keep, no-op on missing log)
- rotate_if_needed (only triggers when size > max_bytes, custom max_bytes,
  custom keep count, no rotation below threshold)
- main CLI (no args → 0 + default path, --path, --max-mb, --keep, --force
  on missing log, --max-mb fractional → int conversion, exit code always 0)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import recorder_log_rotator as m  # noqa: E402

# ---------------------------------------------------------------------------
# _size_bytes
# ---------------------------------------------------------------------------


class TestSizeBytes:
    """Tests for the existence/size probe."""

    def test_returns_int_zero_for_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.log"
        assert m._size_bytes(missing) == 0
        assert isinstance(m._size_bytes(missing), int)

    def test_returns_actual_size(self, tmp_path: Path) -> None:
        p = tmp_path / "present.log"
        p.write_bytes(b"x" * 1234)
        assert m._size_bytes(p) == 1234

    def test_swallows_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If stat() raises OSError for reasons other than missing-file,
        _size_bytes must return 0 (defensive default)."""
        p = tmp_path / "log"
        p.write_bytes(b"data")

        def _boom(_self: Path) -> os.stat_result:  # type: ignore[name-defined]
            raise OSError("synthetic failure")

        monkeypatch.setattr(Path, "stat", _boom)
        assert m._size_bytes(p) == 0

    def test_zero_size_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.log"
        p.write_bytes(b"")
        assert m._size_bytes(p) == 0


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


class TestRotate:
    """Tests for the cascade rotation primitive."""

    def test_missing_log_returns_false(self, tmp_path: Path) -> None:
        assert m.rotate(tmp_path / "absent.log") is False
        # And no spurious files should have been created.
        assert list(tmp_path.iterdir()) == []

    def test_active_log_becomes_dot_one(self, tmp_path: Path) -> None:
        log = tmp_path / "rec.log"
        log.write_text("hello")
        assert m.rotate(log) is True
        assert not log.exists()
        assert (tmp_path / "rec.log.1").read_text() == "hello"

    def test_cascade_pushes_existing_rotations(self, tmp_path: Path) -> None:
        """Pre-existing .1, .2, .3 should be pushed to .2, .3, .4."""
        log = tmp_path / "r.log"
        log.write_text("new")
        (tmp_path / "r.log.1").write_text("first")
        (tmp_path / "r.log.2").write_text("second")
        (tmp_path / "r.log.3").write_text("third")

        assert m.rotate(log, keep=5) is True

        assert not log.exists()
        assert (tmp_path / "r.log.1").read_text() == "new"
        assert (tmp_path / "r.log.2").read_text() == "first"
        assert (tmp_path / "r.log.3").read_text() == "second"
        assert (tmp_path / "r.log.4").read_text() == "third"
        # No .5 because we only had .1-.3 going in.
        assert not (tmp_path / "r.log.5").exists()

    def test_oldest_rotation_dropped_at_keep(self, tmp_path: Path) -> None:
        """With keep=3, a pre-existing .3 must be removed (not pushed to .4)."""
        log = tmp_path / "r.log"
        log.write_text("new")
        for idx in (1, 2, 3):
            (tmp_path / f"r.log.{idx}").write_text(f"old{idx}")

        m.rotate(log, keep=3)

        # The active log moved to .1; .1 → .2, .2 → .3, .3 dropped.
        assert (tmp_path / "r.log.1").read_text() == "new"
        assert (tmp_path / "r.log.2").read_text() == "old1"
        assert (tmp_path / "r.log.3").read_text() == "old2"
        assert not (tmp_path / "r.log.4").exists()

    def test_keep_one_drops_oldest_immediately(self, tmp_path: Path) -> None:
        """With keep=1, there is no .1 slot to push into; the active log
        just takes the .1 slot, and any pre-existing .1 is dropped."""
        log = tmp_path / "r.log"
        log.write_text("brand_new")
        (tmp_path / "r.log.1").write_text("previous")

        m.rotate(log, keep=1)

        assert (tmp_path / "r.log.1").read_text() == "brand_new"
        assert not (tmp_path / "r.log.2").exists()

    def test_does_not_touch_unrelated_files(self, tmp_path: Path) -> None:
        log = tmp_path / "r.log"
        log.write_text("data")
        sibling = tmp_path / "r.log.bak"
        sibling.write_text("keep me")

        m.rotate(log, keep=3)

        assert sibling.read_text() == "keep me"

    def test_uses_os_replace_atomically(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """rotate() must use os.replace (atomic on POSIX), not shutil.move."""
        log = tmp_path / "r.log"
        log.write_text("data")

        seen: list[tuple[Path, Path]] = []
        real_replace = os.replace

        def _spy_replace(src, dst):  # type: ignore[no-untyped-def]
            seen.append((src, dst))
            return real_replace(src, dst)

        monkeypatch.setattr(m.os, "replace", _spy_replace)
        m.rotate(log, keep=3)

        assert seen, "os.replace was not called"
        # The final replace must be the active log → .1.
        assert str(seen[-1][0]).endswith("r.log")
        assert str(seen[-1][1]).endswith("r.log.1")


# ---------------------------------------------------------------------------
# rotate_if_needed
# ---------------------------------------------------------------------------


class TestRotateIfNeeded:
    """Tests for the threshold-gated rotate."""

    def test_below_threshold_no_op(self, tmp_path: Path) -> None:
        log = tmp_path / "r.log"
        log.write_text("tiny")
        assert m.rotate_if_needed(log, max_bytes=1024) is False
        assert log.exists()

    def test_exactly_at_threshold_no_op(self, tmp_path: Path) -> None:
        """`<=` boundary: a file *equal* to max_bytes must NOT rotate."""
        log = tmp_path / "r.log"
        log.write_bytes(b"x" * 100)
        assert m.rotate_if_needed(log, max_bytes=100) is False

    def test_just_over_threshold_rotates(self, tmp_path: Path) -> None:
        log = tmp_path / "r.log"
        log.write_bytes(b"x" * 101)
        assert m.rotate_if_needed(log, max_bytes=100) is True
        assert not log.exists()
        assert (tmp_path / "r.log.1").exists()

    def test_missing_log_no_op(self, tmp_path: Path) -> None:
        assert m.rotate_if_needed(tmp_path / "nope.log") is False

    def test_custom_keep_count(self, tmp_path: Path) -> None:
        """Bumping keep should preserve more history."""
        log = tmp_path / "r.log"
        log.write_bytes(b"x" * 10_000)
        # Pre-seed four rotations.
        for idx in (1, 2, 3, 4):
            (tmp_path / f"r.log.{idx}").write_bytes(b"old")

        m.rotate_if_needed(log, max_bytes=100, keep=5)

        # Five rotations survive (the four old ones plus the new one).
        for idx in (1, 2, 3, 4, 5):
            assert (tmp_path / f"r.log.{idx}").exists()
        # No .6.
        assert not (tmp_path / "r.log.6").exists()

    def test_default_log_path_safe(self, tmp_path: Path) -> None:
        """The default path is ~/OysterRecorder.log; rotate_if_needed
        treats it like any other Path. We pass the path explicitly to
        avoid touching the real home directory (default args are
        evaluated at function definition time, not lookup time)."""
        target = tmp_path / "OysterRecorder.log"
        target.write_bytes(b"x" * 200)
        # Use a small max so rotation triggers.
        assert m.rotate_if_needed(log_path=target, max_bytes=100) is True
        assert (tmp_path / "OysterRecorder.log.1").exists()
        # The default value still resolves to ~/OysterRecorder.log.
        assert str(m.DEFAULT_LOG_PATH).endswith("OysterRecorder.log")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMainCli:
    """Tests for the argparse entry point."""

    def test_no_args_returns_zero(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Default invocation (no CLI args) → exit 0 + `rotated=…` line."""
        monkeypatch.setattr(m.sys, "argv", ["recorder_log_rotator"])
        rc = m.main([])
        out = capsys.readouterr().out
        assert rc == 0
        assert "rotated=" in out
        assert "path=" in out

    def test_explicit_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "explicit.log"
        log.write_bytes(b"x" * 200)
        rc = m.main(["--path", str(log), "--max-mb", "0.0001"])  # ~105 bytes
        out = capsys.readouterr().out
        assert rc == 0
        assert "rotated=True" in out
        assert not log.exists()
        assert (tmp_path / "explicit.log.1").exists()

    def test_force_rotates_below_threshold(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "f.log"
        log.write_text("tiny payload")
        rc = m.main(["--path", str(log), "--force"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "rotated=True" in out
        assert not log.exists()
        assert (tmp_path / "f.log.1").read_text() == "tiny payload"

    def test_force_on_missing_log_reports_false(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "nope.log"
        rc = m.main(["--path", str(log), "--force"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "rotated=False" in out

    def test_no_rotation_below_threshold(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "small.log"
        log.write_bytes(b"x" * 10)
        # 1 MB threshold — log is well below it.
        rc = m.main(["--path", str(log), "--max-mb", "1"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "rotated=False" in out
        assert log.exists()

    def test_max_mb_fractional_to_int(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--max-mb is float, internal conversion must produce an int
        byte count. We exercise a 0.5 MB threshold on a 600 KB log."""
        log = tmp_path / "mb.log"
        log.write_bytes(b"x" * (600 * 1024))
        rc = m.main(["--path", str(log), "--max-mb", "0.5"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "rotated=True" in out

    def test_keep_count_honored(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "k.log"
        log.write_bytes(b"x" * 200)
        for idx in (1, 2, 3):
            (tmp_path / f"k.log.{idx}").write_bytes(b"old")
        rc = m.main(["--path", str(log), "--max-mb", "0.0001", "--keep", "3"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "rotated=True" in out
        # Active + 3 rotations, .4 must not exist.
        assert (tmp_path / "k.log.1").exists()
        assert (tmp_path / "k.log.2").exists()
        assert (tmp_path / "k.log.3").exists()
        assert not (tmp_path / "k.log.4").exists()

    def test_prints_human_readable_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Output format is `rotated=<bool> path=<path>` — pure stdout,
        no JSON, no stderr noise on success."""
        log = tmp_path / "h.log"
        log.write_text("hi")
        rc = m.main(["--path", str(log), "--force"])
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.err == ""
        # Not JSON.
        with pytest.raises(json.JSONDecodeError):
            json.loads(captured.out.strip())
        # Format pinned.
        line = captured.out.strip()
        assert line.startswith("rotated=")
        assert "path=" in line
        assert str(log) in line

    def test_no_args_uses_default_path(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """With no args, the reported `path=` must be DEFAULT_LOG_PATH."""
        fake_default = tmp_path / "DefaultProbe.log"
        monkeypatch.setattr(m, "DEFAULT_LOG_PATH", fake_default)
        rc = m.main([])
        out = capsys.readouterr().out
        assert rc == 0
        assert f"path={fake_default}" in out
