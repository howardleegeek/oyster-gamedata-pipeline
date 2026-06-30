#!/usr/bin/env python3
"""Tests for bin/stamp_real_metadata.py — D15 'oyster-real-screen-capture' metadata stamper.

Covers:
- REAL_COMMENT_TAG constant is the locked string
- stamp_video(missing path) raises FileNotFoundError
- stamp_video on success: builds correct ffmpeg command (ffmpeg -y -i IN -c copy
  -metadata comment=REAL_COMMENT_TAG -metadata composer=oyster-recorder-VERSION OUT),
  returns dict with input / size_bytes_before / size_bytes_after / ffmpeg_returncode
- stamp_video uses default recorder_version 'lite-v0.24.0' when not passed
- stamp_video honors custom recorder_version kwarg
- stamp_video creates temp output in same directory as input (so atomic replace works)
- stamp_video on ffmpeg failure: raises RuntimeError, cleans up the temp file
  (no leftover .tmp-XXXX in dir), does NOT modify the input
- stamp_video cleans up temp file in the success path too (no leftovers)
- stamp_video raises FileNotFoundError BEFORE creating any temp file
- main() happy path: returns 0, prints JSON info
- main() FileNotFoundError path: returns 2, error to stderr
- main() generic exception path: returns 3, error to stderr
- main() honors --recorder-version flag
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import stamp_real_metadata as m  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level constant
# ---------------------------------------------------------------------------


class TestRealCommentTag:
    """Lock the comment string D5 expects to find in a stamped video."""

    def test_constant_is_locked_string(self):
        assert m.REAL_COMMENT_TAG == "oyster-real-screen-capture"

    def test_constant_is_string_type(self):
        assert isinstance(m.REAL_COMMENT_TAG, str)


# ---------------------------------------------------------------------------
# stamp_video — file presence / setup
# ---------------------------------------------------------------------------


class TestStampVideoFilePresence:
    """Input validation: missing path must raise BEFORE creating any temp file."""

    def test_missing_path_raises_filenotfound(self, tmp_path: Path) -> None:
        missing = tmp_path / "no-such-file.mp4"
        with pytest.raises(FileNotFoundError):
            m.stamp_video(missing)

    def test_missing_path_creates_no_temp_files(self, tmp_path: Path) -> None:
        """If the input doesn't exist, no temp/partial files should land in the dir."""
        missing = tmp_path / "no-such-file.mp4"
        with pytest.raises(FileNotFoundError):
            m.stamp_video(missing)
        leftovers = [p for p in tmp_path.iterdir()]
        assert leftovers == [], f"unexpected files: {leftovers}"


# ---------------------------------------------------------------------------
# stamp_video — happy path / ffmpeg command construction
# ---------------------------------------------------------------------------


def _fake_completed_process(returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    """Build a minimal CompletedProcess stand-in for subprocess.run mocking."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr=stderr
    )


class TestStampVideoSuccess:
    """stamp_video: success path returns the right dict and invokes ffmpeg correctly."""

    def test_returns_dict_with_expected_keys(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 256)
        fake = _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", return_value=fake) as run_mock:
            # Need tmp.replace() to actually move, so have subprocess.run side-effect
            # by pre-creating the tmp file
            def fake_run(cmd, **kwargs):
                # The last cmd entry is the output path
                out_path = Path(cmd[-1])
                out_path.write_bytes(b"x" * 512)
                return fake

            run_mock.side_effect = fake_run
            result = m.stamp_video(video)

        assert set(result.keys()) == {
            "input",
            "size_bytes_before",
            "size_bytes_after",
            "ffmpeg_returncode",
        }
        assert result["input"] == str(video)
        assert result["size_bytes_before"] == 256
        assert result["size_bytes_after"] == 512
        assert result["ffmpeg_returncode"] == 0

    def test_ffmpeg_command_includes_copy_no_reencode(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 32)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            out_path = Path(cmd[-1])
            out_path.write_bytes(b"x" * 64)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            m.stamp_video(video)

        cmd = captured["cmd"]
        assert cmd[0] == "ffmpeg"
        assert cmd[1] == "-y"
        assert "-c" in cmd and "copy" in cmd, "ffmpeg must use -c copy (no re-encode)"

    def test_ffmpeg_command_carries_real_comment_metadata(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 16)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            Path(cmd[-1]).write_bytes(b"x" * 32)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            m.stamp_video(video)

        # Find the comment metadata arg
        cmd = captured["cmd"]
        comment_idx = cmd.index("comment=oyster-real-screen-capture")
        # The arg before it must be -metadata
        assert cmd[comment_idx - 1] == "-metadata"

    def test_ffmpeg_command_carries_composer_version(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 16)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            Path(cmd[-1]).write_bytes(b"x" * 32)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            m.stamp_video(video, recorder_version="custom-v9.9.9")

        cmd = captured["cmd"]
        assert "composer=oyster-recorder-custom-v9.9.9" in cmd

    def test_default_recorder_version_is_lite(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 16)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            Path(cmd[-1]).write_bytes(b"x" * 32)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            m.stamp_video(video)  # no recorder_version

        assert "composer=oyster-recorder-lite-v0.24.0" in captured["cmd"]

    def test_temp_output_in_same_dir_as_input(self, tmp_path: Path) -> None:
        """Atomic rename requires the temp file to live in the same filesystem
        as the input — verify tempdir is the input's parent directory."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 16)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            out_path = Path(cmd[-1])
            assert out_path.parent == video.parent, (
                f"temp file {out_path} not in input dir {video.parent}"
            )
            out_path.write_bytes(b"x" * 32)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            m.stamp_video(video)

    def test_temp_output_preserves_input_suffix(self, tmp_path: Path) -> None:
        """The temp file should end in the same suffix as the input so the
        atomic rename does not change the file type (e.g. mkv stays mkv)."""
        for suffix in (".mp4", ".mkv", ".mov"):
            video = tmp_path / f"clip{suffix}"
            video.write_bytes(b"x" * 16)
            captured = {}
            expected_suffix = suffix

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                out_path = Path(cmd[-1])
                assert out_path.suffix == expected_suffix, (
                    f"temp suffix {out_path.suffix} != input {expected_suffix}"
                )
                out_path.write_bytes(b"x" * 32)
                return _fake_completed_process(returncode=0)

            with patch.object(m.subprocess, "run", side_effect=fake_run):
                m.stamp_video(video)

    def test_subprocess_timeout_is_120(self, tmp_path: Path) -> None:
        """The 2-minute ffmpeg timeout must be forwarded to subprocess.run
        so a hung ffmpeg does not stall the stamper forever."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 16)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            Path(cmd[-1]).write_bytes(b"x" * 32)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            m.stamp_video(video)

        assert captured["kwargs"].get("timeout") == 120

    def test_input_string_path_accepted(self, tmp_path: Path) -> None:
        """A bare string path should be coerced to Path, not crash."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 16)

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"x" * 32)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            result = m.stamp_video(str(video))  # str, not Path

        assert result["input"] == str(video)

    def test_no_temp_file_leftover_on_success(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 16)

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"x" * 32)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            m.stamp_video(video)

        # After the atomic replace, the temp file should not linger.
        # (Final directory should only contain the renamed output file.)
        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == ["clip.mp4"], f"unexpected files: {names}"


# ---------------------------------------------------------------------------
# stamp_video — ffmpeg failure path
# ---------------------------------------------------------------------------


class TestStampVideoFailure:
    """stamp_video: ffmpeg non-zero returncode must raise RuntimeError cleanly."""

    def test_ffmpeg_failure_raises_runtimeerror(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 32)

        fake = _fake_completed_process(returncode=1, stderr="boom: invalid codec")
        with patch.object(m.subprocess, "run", return_value=fake):
            with pytest.raises(RuntimeError) as excinfo:
                m.stamp_video(video)
            assert "ffmpeg failed" in str(excinfo.value)
            assert "rc=1" in str(excinfo.value)

    def test_ffmpeg_failure_does_not_modify_input(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"original-bytes-32-bytes-of-data")

        fake = _fake_completed_process(returncode=1, stderr="err")
        with patch.object(m.subprocess, "run", return_value=fake):
            with pytest.raises(RuntimeError):
                m.stamp_video(video)

        # Input file untouched
        assert video.read_bytes() == b"original-bytes-32-bytes-of-data"

    def test_ffmpeg_failure_cleans_up_temp_file(self, tmp_path: Path) -> None:
        """On failure, the temp output must be removed (no .tmp-XXXX leftovers)."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 32)

        def fake_run(cmd, **kwargs):
            # Create the temp file so unlink has something to remove
            Path(cmd[-1]).write_bytes(b"x" * 64)
            return _fake_completed_process(returncode=1, stderr="err")

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            with pytest.raises(RuntimeError):
                m.stamp_video(video)

        # Only the original video should remain
        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == ["clip.mp4"], f"unexpected files: {names}"

    def test_ffmpeg_failure_stderr_truncated_to_500_chars(self, tmp_path: Path) -> None:
        """The exception message should include the last 500 chars of stderr
        (or all of it if shorter)."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 32)

        long_stderr = "A" * 500 + "B" * 500
        fake = _fake_completed_process(returncode=1, stderr=long_stderr)
        with patch.object(m.subprocess, "run", return_value=fake):
            with pytest.raises(RuntimeError) as excinfo:
                m.stamp_video(video)
        msg = str(excinfo.value)
        # Last 500 chars of stderr (all "B") should be present
        assert long_stderr[-500:] in msg
        # Earlier 500 chars (all "A") should NOT be present
        assert long_stderr[:500] not in msg


# ---------------------------------------------------------------------------
# main() — CLI entry point
# ---------------------------------------------------------------------------


class TestMain:
    """main() routes errors to the right exit code and prints info on success."""

    def test_success_prints_json_and_returns_zero(self, tmp_path: Path, capsys) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 32)

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"x" * 64)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            rc = m.main([str(video)])

        assert rc == 0
        captured = capsys.readouterr()
        # stdout is JSON
        info = json.loads(captured.out)
        assert info["ffmpeg_returncode"] == 0
        assert info["input"] == str(video)

    def test_filenotfound_returns_2(self, tmp_path: Path, capsys) -> None:
        missing = tmp_path / "nope.mp4"
        rc = m.main([str(missing)])

        assert rc == 2
        captured = capsys.readouterr()
        assert "input not found" in captured.err
        assert str(missing) in captured.err

    def test_runtimeerror_returns_3(self, tmp_path: Path, capsys) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 32)
        fake = _fake_completed_process(returncode=1, stderr="oops")
        with patch.object(m.subprocess, "run", return_value=fake):
            rc = m.main([str(video)])

        assert rc == 3
        captured = capsys.readouterr()
        assert "RuntimeError" in captured.err
        assert "oops" in captured.err

    def test_recorder_version_flag_forwarded(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 16)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            Path(cmd[-1]).write_bytes(b"x" * 32)
            return _fake_completed_process(returncode=0)

        with patch.object(m.subprocess, "run", side_effect=fake_run):
            rc = m.main([str(video), "--recorder-version", "pro-v1.2.3"])

        assert rc == 0
        assert "composer=oyster-recorder-pro-v1.2.3" in captured["cmd"]

    def test_no_args_exits_with_argparse_error(self, capsys) -> None:
        """Calling main() with no args should hit argparse's required-arg path."""
        with pytest.raises(SystemExit) as excinfo:
            m.main([])
        assert excinfo.value.code == 2  # argparse standard for usage error
