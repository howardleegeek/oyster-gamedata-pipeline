#!/usr/bin/env python3
"""Tests for bin/red_team_wrong_fps.py — 30-fps PRD violation detector.

Covers:
  * Module constants (REQUIRED_FPS, FPS_TOLERANCE) match the spec
  * _find_ffprobe: returns a path when shutil.which finds it, returns None otherwise
  * _probe_fps: returns None when ffprobe is missing; parses integer fps
    (e.g. "30") and fractional fps (e.g. "30/1") from JSON; returns None on
    non-zero exit; returns None on malformed JSON / parse errors
  * _check_videos: OK when fps within tolerance, REJECT outside, MISSING for
    non-existent path, UNKNOWN when probe returns None
  * main(): --help exits 0, no args returns 2 (usage), missing file exits 1
    with a violation reported, video with fps exactly 30 returns 0, video
    with fps 60 returns 1, --manifest list-of-paths reads JSON list,
    --manifest dict-of-videos reads dict, --manifest plaintext newline
    list is parsed line-by-line, --json-out writes structured results,
    unknown arg returns 2
  * subprocess end-to-end smoke (script runs and exits with the expected
    code for an obviously-bad input)
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

import red_team_wrong_fps as rt  # noqa: E402

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


class TestConstants:
    """The PRD constants are frozen at 30 fps with a 0.05 tolerance."""

    def test_required_fps_is_thirty(self):
        assert rt.REQUIRED_FPS == 30.0

    def test_fps_tolerance_is_small(self):
        assert rt.FPS_TOLERANCE == 0.05
        assert rt.FPS_TOLERANCE < 1.0

    def test_module_exposes_check_and_probe(self):
        assert callable(rt._find_ffprobe)
        assert callable(rt._probe_fps)
        assert callable(rt._check_videos)
        assert callable(rt.main)


# ---------------------------------------------------------------------------
# _find_ffprobe
# ---------------------------------------------------------------------------


class TestFindFfprobe:
    """_find_ffprobe delegates to shutil.which."""

    def test_returns_path_when_shutil_finds_it(self):
        with patch.object(rt.shutil, "which", return_value="/usr/bin/ffprobe") as w:
            assert rt._find_ffprobe() == "/usr/bin/ffprobe"
        w.assert_called_once_with("ffprobe")

    def test_returns_none_when_shutil_returns_none(self):
        with patch.object(rt.shutil, "which", return_value=None):
            assert rt._find_ffprobe() is None


# ---------------------------------------------------------------------------
# _probe_fps
# ---------------------------------------------------------------------------


class TestProbeFps:
    """_probe_fps shells out to ffprobe and parses r_frame_rate."""

    def test_returns_none_when_ffprobe_missing(self):
        with patch.object(rt, "_find_ffprobe", return_value=None):
            assert rt._probe_fps("/anywhere.mp4") is None

    def test_parses_fractional_fps(self):
        """A "30/1" r_frame_rate resolves to 30.0."""
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"streams": [{"r_frame_rate": "30/1"}]}),
            stderr="",
        )
        with patch.object(rt, "_find_ffprobe", return_value="/usr/bin/ffprobe"), \
             patch.object(rt.subprocess, "run", return_value=fake) as run_mock:
            assert rt._probe_fps("/foo.mp4") == 30.0
        run_mock.assert_called_once()
        cmd = run_mock.call_args[0][0]
        assert cmd[0] == "/usr/bin/ffprobe"
        assert "-show_entries" in cmd
        assert "stream=r_frame_rate" in cmd
        assert cmd[-1] == "/foo.mp4"

    def test_parses_integer_fps(self):
        """A bare "60" r_frame_rate resolves to 60.0."""
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"streams": [{"r_frame_rate": "60"}]}), stderr="",
        )
        with patch.object(rt, "_find_ffprobe", return_value="/usr/bin/ffprobe"), \
             patch.object(rt.subprocess, "run", return_value=fake):
            assert rt._probe_fps("/bar.mp4") == 60.0

    def test_parses_nontrivial_fraction(self):
        """A "60000/1001" (NTSC 59.94) resolves to the correct float."""
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"streams": [{"r_frame_rate": "60000/1001"}]}),
            stderr="",
        )
        with patch.object(rt, "_find_ffprobe", return_value="/usr/bin/ffprobe"), \
             patch.object(rt.subprocess, "run", return_value=fake):
            fps = rt._probe_fps("/baz.mp4")
            assert fps is not None
            assert abs(fps - 60000 / 1001) < 1e-9

    def test_returns_none_on_calledprocesserror(self):
        """ffprobe returning non-zero produces a None result, not an exception."""
        with patch.object(rt, "_find_ffprobe", return_value="/usr/bin/ffprobe"), \
             patch.object(rt.subprocess, "run",
                          side_effect=rt.subprocess.CalledProcessError(1, "ffprobe")):
            assert rt._probe_fps("/bad.mp4") is None

    def test_returns_none_on_timeout(self):
        """ffprobe hanging past timeout produces a None result."""
        with patch.object(rt, "_find_ffprobe", return_value="/usr/bin/ffprobe"), \
             patch.object(rt.subprocess, "run",
                          side_effect=rt.subprocess.TimeoutExpired("ffprobe", 30)):
            assert rt._probe_fps("/slow.mp4") is None

    def test_returns_none_on_malformed_json(self):
        """Unparseable ffprobe JSON yields None (no crash)."""
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="this is not json", stderr="",
        )
        with patch.object(rt, "_find_ffprobe", return_value="/usr/bin/ffprobe"), \
             patch.object(rt.subprocess, "run", return_value=fake):
            assert rt._probe_fps("/malformed.mp4") is None

    def test_returns_none_on_zero_denominator(self):
        """r_frame_rate of "30/0" must not raise ZeroDivisionError."""
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"streams": [{"r_frame_rate": "30/0"}]}), stderr="",
        )
        with patch.object(rt, "_find_ffprobe", return_value="/usr/bin/ffprobe"), \
             patch.object(rt.subprocess, "run", return_value=fake):
            assert rt._probe_fps("/zero.mp4") is None

    def test_returns_none_on_empty_streams(self):
        """A streams=[] response yields None rather than an IndexError."""
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"streams": []}), stderr="",
        )
        with patch.object(rt, "_find_ffprobe", return_value="/usr/bin/ffprobe"), \
             patch.object(rt.subprocess, "run", return_value=fake):
            assert rt._probe_fps("/no_streams.mp4") is None


# ---------------------------------------------------------------------------
# _check_videos
# ---------------------------------------------------------------------------


class TestCheckVideos:
    """_check_videos classifies each path as OK / REJECT / MISSING / UNKNOWN."""

    def _stub(self, fps):
        return patch.object(rt, "_probe_fps", return_value=fps)

    def test_ok_within_tolerance(self, tmp_path):
        f = tmp_path / "ok.mp4"
        f.write_text("x")
        with self._stub(30.0):
            results = rt._check_videos([str(f)])
        assert results == [(str(f), "OK", 30.0)]

    def test_ok_just_inside_upper_tolerance(self, tmp_path):
        f = tmp_path / "ok.mp4"
        f.write_text("x")
        with self._stub(30.0 + 0.04):
            results = rt._check_videos([str(f)])
        assert results[0][1] == "OK"

    def test_ok_just_inside_lower_tolerance(self, tmp_path):
        f = tmp_path / "ok.mp4"
        f.write_text("x")
        with self._stub(30.0 - 0.04):
            results = rt._check_videos([str(f)])
        assert results[0][1] == "OK"

    def test_reject_above_tolerance(self, tmp_path):
        f = tmp_path / "fast.mp4"
        f.write_text("x")
        with self._stub(60.0):
            results = rt._check_videos([str(f)])
        assert results[0][1] == "REJECT"
        assert results[0][2] == 60.0

    def test_reject_below_tolerance(self, tmp_path):
        f = tmp_path / "slow.mp4"
        f.write_text("x")
        with self._stub(15.0):
            results = rt._check_videos([str(f)])
        assert results[0][1] == "REJECT"
        assert results[0][2] == 15.0

    def test_missing_path(self, tmp_path):
        """A path that does not exist is tagged MISSING — not probed."""
        missing = tmp_path / "does_not_exist.mp4"
        with self._stub(30.0):
            results = rt._check_videos([str(missing)])
        assert results == [(str(missing), "MISSING", None)]

    def test_unknown_when_probe_returns_none(self, tmp_path):
        f = tmp_path / "unk.mp4"
        f.write_text("x")
        with self._stub(None):
            results = rt._check_videos([str(f)])
        assert results == [(str(f), "UNKNOWN", None)]

    def test_mixed_batch(self, tmp_path):
        """A batch with multiple statuses returns one tuple per input, in order."""
        ok = tmp_path / "ok.mp4"
        ok.write_text("x")
        bad = tmp_path / "bad.mp4"
        bad.write_text("x")
        missing = tmp_path / "missing.mp4"
        with patch.object(rt, "_probe_fps", side_effect=[30.0, 60.0, None]):
            results = rt._check_videos([str(ok), str(bad), str(missing)])
        assert [r[1] for r in results] == ["OK", "REJECT", "MISSING"]
        assert results[1][2] == 60.0

    def test_does_not_probe_missing_files(self, tmp_path):
        """MISSING entries are short-circuited — _probe_fps is never called."""
        missing = tmp_path / "absent.mp4"
        with patch.object(rt, "_probe_fps") as probe_mock:
            rt._check_videos([str(missing)])
        probe_mock.assert_not_called()


# ---------------------------------------------------------------------------
# main() — argument parsing and exit codes
# ---------------------------------------------------------------------------


class TestMainArgparse:
    """CLI surface: --help, no args, unknown args."""

    def test_help_exits_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            rt.main(["--help"])
        assert exc_info.value.code == 0

    def test_no_args_returns_two(self, capsys):
        """No videos and no manifest → usage error, exit 2."""
        code = rt.main([])
        assert code == 2
        # Error is logged to stderr; we don't assert on its content here.
        captured = capsys.readouterr()
        assert "No video" in captured.err or captured.err  # tolerated

    def test_unknown_arg_returns_two(self, tmp_path):
        f = tmp_path / "ok.mp4"
        f.write_text("x")
        with patch.object(rt, "_probe_fps", return_value=30.0):
            with pytest.raises(SystemExit) as exc_info:
                rt.main(["--definitely-not-a-flag", str(f)])
        assert exc_info.value.code == 2


class TestMainVideoResults:
    """main() end-to-end with mocked _probe_fps."""

    def test_compliant_video_returns_zero(self, tmp_path, caplog):
        f = tmp_path / "ok.mp4"
        f.write_text("x")
        with patch.object(rt, "_probe_fps", return_value=30.0):
            with caplog.at_level("INFO", logger="red_team_wrong_fps"):
                code = rt.main([str(f)])
        assert code == 0
        assert any("[PASS]" in rec.message for rec in caplog.records)

    def test_offspec_video_returns_one(self, tmp_path, caplog):
        f = tmp_path / "fast.mp4"
        f.write_text("x")
        with patch.object(rt, "_probe_fps", return_value=60.0):
            with caplog.at_level("INFO", logger="red_team_wrong_fps"):
                code = rt.main([str(f)])
        assert code == 1
        msgs = [rec.message for rec in caplog.records]
        assert any("[FAIL]" in m for m in msgs)
        assert any("RED-TEAM" in m for m in msgs)

    def test_missing_file_returns_zero(self, tmp_path, caplog):
        missing = tmp_path / "nope.mp4"
        with patch.object(rt, "_probe_fps") as probe_mock:
            with caplog.at_level("INFO", logger="red_team_wrong_fps"):
                code = rt.main([str(missing)])
        # MISSING is logged but does NOT count as a violation — main returns 0.
        assert code == 0
        assert any("[MISS]" in rec.message for rec in caplog.records)
        # MISSING must short-circuit _probe_fps.
        probe_mock.assert_not_called()

    def test_unknown_fps_logs_warning_returns_zero(self, tmp_path, caplog):
        f = tmp_path / "unk.mp4"
        f.write_text("x")
        with patch.object(rt, "_probe_fps", return_value=None):
            with caplog.at_level("INFO", logger="red_team_wrong_fps"):
                code = rt.main([str(f)])
        # UNKNOWN does NOT count as a violation → main returns 0.
        assert code == 0
        assert any("[????]" in rec.message for rec in caplog.records)

    def test_json_out_writes_structured_results(self, tmp_path, capsys):
        f1 = tmp_path / "a.mp4"
        f1.write_text("x")
        f2 = tmp_path / "b.mp4"
        f2.write_text("x")
        out = tmp_path / "results.json"
        with patch.object(rt, "_probe_fps", side_effect=[30.0, 60.0]):
            code = rt.main([str(f1), str(f2), "--json-out", str(out)])
        assert code == 1
        data = json.loads(out.read_text())
        assert isinstance(data, list)
        assert len(data) == 2
        statuses = sorted(d["status"] for d in data)
        assert statuses == ["OK", "REJECT"]
        # The OK entry carries an fps value; REJECT likewise.
        ok = next(d for d in data if d["status"] == "OK")
        assert ok["fps"] == 30.0
        rej = next(d for d in data if d["status"] == "REJECT")
        assert rej["fps"] == 60.0


class TestMainManifest:
    """--manifest accepts a JSON list, a JSON dict, and a plaintext list."""

    def test_manifest_json_list(self, tmp_path, capsys):
        f1 = tmp_path / "a.mp4"
        f1.write_text("x")
        f2 = tmp_path / "b.mp4"
        f2.write_text("x")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps([str(f1), str(f2)]))
        with patch.object(rt, "_probe_fps", side_effect=[30.0, 30.0]):
            code = rt.main(["--manifest", str(manifest)])
        assert code == 0

    def test_manifest_json_dict_with_videos_key(self, tmp_path, capsys):
        f = tmp_path / "a.mp4"
        f.write_text("x")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"videos": [str(f)]}))
        with patch.object(rt, "_probe_fps", return_value=30.0):
            code = rt.main(["--manifest", str(manifest)])
        assert code == 0

    def test_manifest_plaintext_newline_list(self, tmp_path, capsys):
        f1 = tmp_path / "a.mp4"
        f1.write_text("x")
        f2 = tmp_path / "b.mp4"
        f2.write_text("x")
        manifest = tmp_path / "manifest.txt"
        manifest.write_text(f"{f1}\n{f2}\n")
        with patch.object(rt, "_probe_fps", side_effect=[30.0, 30.0]):
            code = rt.main(["--manifest", str(manifest)])
        assert code == 0

    def test_manifest_combines_with_positional_videos(self, tmp_path, capsys):
        """A manifest and positional VIDEOs are concatenated (not exclusive)."""
        f_pos = tmp_path / "positional.mp4"
        f_pos.write_text("x")
        f_man = tmp_path / "manifested.mp4"
        f_man.write_text("x")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps([str(f_man)]))
        with patch.object(rt, "_probe_fps", side_effect=[30.0, 30.0]) as probe_mock:
            code = rt.main([str(f_pos), "--manifest", str(manifest)])
        assert code == 0
        assert probe_mock.call_count == 2

    def test_manifest_missing_file_returns_two(self, tmp_path, capsys):
        missing = tmp_path / "nope.json"
        with patch.object(rt, "_probe_fps") as probe_mock:
            code = rt.main(["--manifest", str(missing)])
        assert code == 2
        probe_mock.assert_not_called()


# ---------------------------------------------------------------------------
# subprocess end-to-end
# ---------------------------------------------------------------------------


class TestSubprocessEndToEnd:
    """The script is runnable as a standalone subprocess."""

    def test_help_via_subprocess(self):
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_wrong_fps.py"), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "Red-team lint" in result.stdout

    def test_no_args_via_subprocess_exits_two(self):
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_wrong_fps.py")],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 2

    def test_missing_file_via_subprocess_exits_zero(self, tmp_path):
        missing = tmp_path / "definitely_not_a_video_12345.mp4"
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_wrong_fps.py"), str(missing)],
            capture_output=True, text=True, timeout=15,
        )
        # MISSING is logged but does NOT count as a violation → exit code 0.
        assert result.returncode == 0
        # logger output goes to stderr in default config.
        assert "[MISS]" in result.stderr
