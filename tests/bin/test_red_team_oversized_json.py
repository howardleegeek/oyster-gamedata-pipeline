#!/usr/bin/env python3
"""Tests for bin/red_team_oversized_json.py — OOM-resistant JSON streamer.

Covers:

- generate_record: schema keys, deterministic id format, sensor types.
- generate_oversized_json: file is created, record count matches request,
  size roughly matches target, JSON parses, all records present.
- stream_records: yields dicts, count matches input, empty list when key
  missing, empty list when array absent.
- test_memory_usage: small file passes with reasonable limit, fixed bug
  where peak_memory was never updated (would silently pass even with
  max_mb=0), per-record loop iterates the expected number of times, and
  the limit threshold is enforced (file uses more than 0MB).
- main: --help exits 0, default args, --size-mb, --output-dir, --keep-file
  preserves the JSON, --max-memory-mb, unknown arg SystemExit, subprocess
  end-to-end.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tracemalloc
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

_BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
_SPEC = importlib.util.spec_from_file_location(
    "red_team_oversized_json", _BIN_DIR / "red_team_oversized_json.py"
)
assert _SPEC is not None and _SPEC.loader is not None
red_team_oversized_json = importlib.util.module_from_spec(_SPEC)
sys.modules["red_team_oversized_json"] = red_team_oversized_json
_SPEC.loader.exec_module(red_team_oversized_json)


# ---------------------------------------------------------------------------
# generate_record tests
# ---------------------------------------------------------------------------


class TestGenerateRecord:
    """Tests for generate_record() helper."""

    def test_returns_dict(self) -> None:
        """generate_record returns a dict."""
        rec = red_team_oversized_json.generate_record(0)
        assert isinstance(rec, dict)

    def test_required_top_level_keys(self) -> None:
        """Top-level keys are id, model, firmware, recording, sensors, metadata."""
        rec = red_team_oversized_json.generate_record(0)
        assert set(rec.keys()) == {
            "id",
            "model",
            "firmware",
            "recording",
            "sensors",
            "metadata",
        }

    def test_id_format(self) -> None:
        """id field is zero-padded to width 8."""
        rec = red_team_oversized_json.generate_record(42)
        assert rec["id"] == "cam_00000042"
        assert rec["id"].startswith("cam_")

    def test_recording_keys(self) -> None:
        """recording contains start_time, duration_sec, resolution, fps."""
        rec = red_team_oversized_json.generate_record(0)
        assert set(rec["recording"].keys()) == {
            "start_time",
            "duration_sec",
            "resolution",
            "fps",
        }

    def test_resolution_rotates(self) -> None:
        """resolution cycles through 4K / 1080p / 720p."""
        rec0 = red_team_oversized_json.generate_record(0)
        rec1 = red_team_oversized_json.generate_record(1)
        rec2 = red_team_oversized_json.generate_record(2)
        assert rec0["recording"]["resolution"] == "4K"
        assert rec1["recording"]["resolution"] == "1080p"
        assert rec2["recording"]["resolution"] == "720p"

    def test_fps_rotates(self) -> None:
        """fps cycles through 30 / 60 / 120."""
        rec0 = red_team_oversized_json.generate_record(0)
        rec1 = red_team_oversized_json.generate_record(1)
        rec2 = red_team_oversized_json.generate_record(2)
        assert rec0["recording"]["fps"] == 30
        assert rec1["recording"]["fps"] == 60
        assert rec2["recording"]["fps"] == 120

    def test_sensor_keys(self) -> None:
        """sensors contains accelerometer and gps."""
        rec = red_team_oversized_json.generate_record(0)
        assert set(rec["sensors"].keys()) == {"accelerometer", "gps"}

    def test_index_zero_no_off_by_one(self) -> None:
        """Index 0 produces well-formed start_time (Jan 1, not Dec 32)."""
        rec = red_team_oversized_json.generate_record(0)
        assert rec["recording"]["start_time"].startswith("2024-01-01T")
        assert rec["recording"]["start_time"].endswith(":00:00Z")


# ---------------------------------------------------------------------------
# generate_oversized_json tests
# ---------------------------------------------------------------------------


class TestGenerateOversizedJson:
    """Tests for generate_oversized_json() function."""

    def test_creates_file(self, tmp_path: Path) -> None:
        """File is created at the given path."""
        out = tmp_path / "test.json"
        red_team_oversized_json.generate_oversized_json(out, 4096)
        assert out.exists()

    def test_record_count_matches_request(self, tmp_path: Path) -> None:
        """Number of records written matches target_bytes / RECORD_SIZE_BYTES."""
        out = tmp_path / "test.json"
        target = red_team_oversized_json.RECORD_SIZE_BYTES * 5
        count, _size = red_team_oversized_json.generate_oversized_json(out, target)
        assert count == 5

    def test_returns_count_and_size(self, tmp_path: Path) -> None:
        """Return value is (count, size_bytes) where size_bytes > 0."""
        out = tmp_path / "test.json"
        count, size = red_team_oversized_json.generate_oversized_json(out, 4096)
        assert count > 0
        assert size > 0
        # File size on disk matches reported size
        assert size == out.stat().st_size

    def test_json_is_valid(self, tmp_path: Path) -> None:
        """Output file is parseable JSON."""
        out = tmp_path / "test.json"
        red_team_oversized_json.generate_oversized_json(out, 4096)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        assert "action_cameras" in data
        assert isinstance(data["action_cameras"], list)

    def test_all_records_preserved(self, tmp_path: Path) -> None:
        """Every generated record is present in the file."""
        out = tmp_path / "test.json"
        target = red_team_oversized_json.RECORD_SIZE_BYTES * 3
        red_team_oversized_json.generate_oversized_json(out, target)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        ids = [r["id"] for r in data["action_cameras"]]
        assert ids == ["cam_00000000", "cam_00000001", "cam_00000002"]

    def test_size_proportional_to_records(self, tmp_path: Path) -> None:
        """Generated file size scales with the number of records requested."""
        out_small = tmp_path / "small.json"
        out_large = tmp_path / "large.json"
        small_target = red_team_oversized_json.RECORD_SIZE_BYTES * 2
        large_target = red_team_oversized_json.RECORD_SIZE_BYTES * 8
        _sc, size_small = red_team_oversized_json.generate_oversized_json(
            out_small, small_target
        )
        _lc, size_large = red_team_oversized_json.generate_oversized_json(
            out_large, large_target
        )
        # 4x the records → ~4x the file size
        assert size_large > size_small * 3
        # Records are ~370B each; file should be at least 100B per record
        assert size_small > 100 * 2
        assert size_large > 100 * 8


# ---------------------------------------------------------------------------
# stream_records tests
# ---------------------------------------------------------------------------


class TestStreamRecords:
    """Tests for stream_records() function."""

    def test_yields_dicts(self, tmp_path: Path) -> None:
        """stream_records yields dict objects."""
        path = tmp_path / "test.json"
        path.write_text(json.dumps({"action_cameras": [{"x": 1}, {"x": 2}]}))
        records = list(red_team_oversized_json.stream_records(path))
        assert len(records) == 2
        assert all(isinstance(r, dict) for r in records)

    def test_count_matches(self, tmp_path: Path) -> None:
        """Yielded record count matches input array length."""
        path = tmp_path / "test.json"
        path.write_text(json.dumps({"action_cameras": [{"i": i} for i in range(7)]}))
        records = list(red_team_oversized_json.stream_records(path))
        assert len(records) == 7

    def test_empty_action_cameras(self, tmp_path: Path) -> None:
        """Empty action_cameras array yields nothing."""
        path = tmp_path / "test.json"
        path.write_text(json.dumps({"action_cameras": []}))
        records = list(red_team_oversized_json.stream_records(path))
        assert records == []

    def test_missing_action_cameras_key(self, tmp_path: Path) -> None:
        """Missing action_cameras key yields nothing (no KeyError)."""
        path = tmp_path / "test.json"
        path.write_text(json.dumps({"other": []}))
        records = list(red_team_oversized_json.stream_records(path))
        assert records == []

    def test_is_iterator(self, tmp_path: Path) -> None:
        """stream_records returns an iterator (not a list)."""
        path = tmp_path / "test.json"
        path.write_text(json.dumps({"action_cameras": []}))
        result = red_team_oversized_json.stream_records(path)
        assert iter(result) is result


# ---------------------------------------------------------------------------
# test_memory_usage tests — includes regression for the fixed bug
# ---------------------------------------------------------------------------


class TestMemoryUsage:
    """Tests for test_memory_usage() function — includes regression for the
    bug where peak_memory was never updated inside the loop, causing the
    memory limit check to be evaluated against 0 / BYTES_PER_MB = 0.0 MB
    regardless of actual usage.
    """

    def test_small_file_passes(self, tmp_path: Path) -> None:
        """Small file with a generous limit returns True."""
        path = tmp_path / "test.json"
        red_team_oversized_json.generate_oversized_json(
            path, red_team_oversized_json.RECORD_SIZE_BYTES * 5
        )
        ok, msg, peak = red_team_oversized_json.test_memory_usage(path, max_mb=1000)
        assert ok is True
        assert "Processed 5 records" in msg
        assert peak > 0  # not the broken 0 — fix is wired up

    def test_record_count_in_message(self, tmp_path: Path) -> None:
        """Success message includes the count of records processed."""
        path = tmp_path / "test.json"
        red_team_oversized_json.generate_oversized_json(
            path, red_team_oversized_json.RECORD_SIZE_BYTES * 4
        )
        _ok, msg, _peak = red_team_oversized_json.test_memory_usage(path, max_mb=1000)
        assert "Processed 4 records" in msg

    def test_bug_fix_zero_limit_fails(self, tmp_path: Path) -> None:
        """Regression: with the bug, max_mb=0 would silently pass because
        peak_mb was always 0.0. After the fix, peak_mb > 0 and the
        limit check should fail.
        """
        path = tmp_path / "test.json"
        red_team_oversized_json.generate_oversized_json(
            path, red_team_oversized_json.RECORD_SIZE_BYTES * 3
        )
        ok, _msg, peak = red_team_oversized_json.test_memory_usage(path, max_mb=0)
        assert ok is False, (
            "REGRESSION: test_memory_usage passed with max_mb=0 — "
            "peak_memory is no longer being tracked (bug not fixed)."
        )
        assert peak > 0

    def test_peak_returned_matches_actual(self, tmp_path: Path) -> None:
        """Returned peak value matches what tracemalloc observed."""
        path = tmp_path / "test.json"
        red_team_oversized_json.generate_oversized_json(
            path, red_team_oversized_json.RECORD_SIZE_BYTES * 2
        )
        # Wrap tracemalloc to capture the highest peak the function saw
        observed_peaks: list[int] = []

        original_start = tracemalloc.start
        original_stop = tracemalloc.stop
        original_get = tracemalloc.get_traced_memory

        def _wrap_get():
            result = original_get()
            observed_peaks.append(result[1])
            return result

        with mock.patch.object(tracemalloc, "start", original_start), \
             mock.patch.object(tracemalloc, "stop", original_stop), \
             mock.patch.object(tracemalloc, "get_traced_memory", _wrap_get):
            _ok, _msg, returned_peak = red_team_oversized_json.test_memory_usage(
                path, max_mb=1000
            )
        # Returned peak should equal at least one of the observed peaks
        assert returned_peak > 0
        assert returned_peak in observed_peaks

    def test_failure_message_includes_limit(self, tmp_path: Path) -> None:
        """When peak exceeds limit, the message includes the limit value."""
        path = tmp_path / "test.json"
        red_team_oversized_json.generate_oversized_json(
            path, red_team_oversized_json.RECORD_SIZE_BYTES * 2
        )
        _ok, msg, _peak = red_team_oversized_json.test_memory_usage(path, max_mb=0)
        assert "0MB" in msg
        assert "exceeds limit" in msg

    def test_tracemalloc_always_stopped(self, tmp_path: Path) -> None:
        """tracemalloc.stop() is called even on the success path (no leak)."""
        path = tmp_path / "test.json"
        red_team_oversized_json.generate_oversized_json(
            path, red_team_oversized_json.RECORD_SIZE_BYTES * 2
        )
        red_team_oversized_json.test_memory_usage(path, max_mb=1000)
        # If tracemalloc was not stopped, is_tracing() returns True
        assert not tracemalloc.is_tracing()


# ---------------------------------------------------------------------------
# main() CLI tests
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for main() CLI entry point."""

    def test_help_exits_zero(self) -> None:
        """--help should exit with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            red_team_oversized_json.main(["--help"])
        assert exc_info.value.code == 0

    def test_unknown_arg_systemexit(self) -> None:
        """Unknown args should raise SystemExit (argparse default)."""
        with pytest.raises(SystemExit):
            red_team_oversized_json.main(["--nonexistent-flag"])

    def test_keep_file_preserves_json(self, tmp_path: Path) -> None:
        """--keep-file preserves the generated JSON on disk."""
        out_dir = tmp_path / "out"
        rc = red_team_oversized_json.main(
            [
                "--size-mb",
                "1",
                "--output-dir",
                str(out_dir),
                "--max-memory-mb",
                "1000",
                "--keep-file",
            ]
        )
        json_path = out_dir / "action_camera.json"
        assert json_path.exists()
        # Should be parseable JSON
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "action_cameras" in data
        assert rc in (0, 1)

    def test_no_keep_file_removes_json(self, tmp_path: Path) -> None:
        """Without --keep-file, the JSON file is removed (cleanup)."""
        out_dir = tmp_path / "out"
        red_team_oversized_json.main(
            [
                "--size-mb",
                "1",
                "--output-dir",
                str(out_dir),
                "--max-memory-mb",
                "1000",
            ]
        )
        # Either the file is gone, or its parent dir is gone
        json_path = out_dir / "action_camera.json"
        assert not json_path.exists() or not out_dir.exists()

    def test_output_dir_created(self, tmp_path: Path) -> None:
        """--output-dir is created if it does not exist."""
        out_dir = tmp_path / "new_dir" / "nested"
        assert not out_dir.exists()
        red_team_oversized_json.main(
            [
                "--size-mb",
                "1",
                "--output-dir",
                str(out_dir),
                "--max-memory-mb",
                "1000",
                "--keep-file",
            ]
        )
        assert out_dir.exists()
        assert (out_dir / "action_camera.json").exists()

    def test_default_args(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default args (no flags) use DEFAULT_SIZE_MB and default memory limit."""
        # Run with a small default to keep CI fast
        monkeypatch.setattr(red_team_oversized_json, "DEFAULT_SIZE_MB", 1)
        rc = red_team_oversized_json.main(
            [
                "--output-dir",
                str(tmp_path / "out"),
                "--max-memory-mb",
                "1000",
                "--keep-file",
            ]
        )
        assert rc in (0, 1)
        assert (tmp_path / "out" / "action_camera.json").exists()


# ---------------------------------------------------------------------------
# Subprocess end-to-end tests
# ---------------------------------------------------------------------------


class TestSubprocess:
    """End-to-end subprocess tests."""

    def test_help_subprocess(self) -> None:
        """--help via subprocess should exit 0."""
        result = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_oversized_json.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Oversized" in result.stdout or "JSON" in result.stdout

    def test_invalid_arg_subprocess(self) -> None:
        """Unknown arg via subprocess should exit non-zero."""
        result = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_oversized_json.py"),
                "--no-such-flag",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_end_to_end_subprocess(self, tmp_path: Path) -> None:
        """End-to-end run with small size and keep-file should succeed."""
        result = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_oversized_json.py"),
                "--size-mb",
                "1",
                "--output-dir",
                str(tmp_path),
                "--max-memory-mb",
                "1000",
                "--keep-file",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "PASS" in result.stdout
        assert (tmp_path / "action_camera.json").exists()
