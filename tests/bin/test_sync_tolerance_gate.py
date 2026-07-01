#!/usr/bin/env python3
"""Tests for bin/sync_tolerance_gate.py."""

import json
import sys
from pathlib import Path

import pytest

# Add parent to path for import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bin.sync_tolerance_gate import (
    calculate_gaps,
    calculate_ratios,
    determine_verdict,
    format_human_readable,
    format_json_output,
    read_camera_frames,
    read_game_ticks,
)


class TestReadCameraFrames:
    """Tests for read_camera_frames function."""

    def test_read_single_camera_file(self, tmp_path):
        """Test reading a single action_camera file."""
        camera_file = tmp_path / "action_camera_000.jsonl"
        camera_file.write_text(
            json.dumps({"frame_id": 1, "timestamp_ns": 1000000000}) + "\n"
            + json.dumps({"frame_id": 2, "timestamp_ns": 2000000000}) + "\n"
        )

        frames = read_camera_frames(tmp_path)
        assert len(frames) == 2
        assert frames[0] == (1, 1000000000)
        assert frames[1] == (2, 2000000000)

    def test_read_multiple_camera_files(self, tmp_path):
        """Test reading multiple action_camera files."""
        (tmp_path / "action_camera_000.jsonl").write_text(
            json.dumps({"frame_id": 1, "timestamp_ns": 1000000000}) + "\n"
        )
        (tmp_path / "action_camera_001.jsonl").write_text(
            json.dumps({"frame_id": 2, "timestamp_ns": 2000000000}) + "\n"
        )

        frames = read_camera_frames(tmp_path)
        assert len(frames) == 2

    def test_read_sorted_by_timestamp(self, tmp_path):
        """Test frames are sorted by timestamp_ns."""
        camera_file = tmp_path / "action_camera_000.jsonl"
        camera_file.write_text(
            json.dumps({"frame_id": 3, "timestamp_ns": 3000000000}) + "\n"
            + json.dumps({"frame_id": 1, "timestamp_ns": 1000000000}) + "\n"
            + json.dumps({"frame_id": 2, "timestamp_ns": 2000000000}) + "\n"
        )

        frames = read_camera_frames(tmp_path)
        assert frames[0][1] == 1000000000
        assert frames[1][1] == 2000000000
        assert frames[2][1] == 3000000000

    def test_ignore_missing_fields(self, tmp_path):
        """Test that frames with missing fields are skipped."""
        camera_file = tmp_path / "action_camera_000.jsonl"
        camera_file.write_text(
            json.dumps({"frame_id": 1, "timestamp_ns": 1000000000}) + "\n"
            + json.dumps({"frame_id": 2}) + "\n"  # missing timestamp_ns
            + json.dumps({"timestamp_ns": 3000000000}) + "\n"  # missing frame_id
        )

        frames = read_camera_frames(tmp_path)
        assert len(frames) == 1
        assert frames[0] == (1, 1000000000)

    def test_ignore_empty_lines(self, tmp_path):
        """Test that empty lines are skipped."""
        camera_file = tmp_path / "action_camera_000.jsonl"
        camera_file.write_text(
            json.dumps({"frame_id": 1, "timestamp_ns": 1000000000})
            + "\n\n"
            + json.dumps({"frame_id": 2, "timestamp_ns": 2000000000})
            + "\n"
        )

        frames = read_camera_frames(tmp_path)
        assert len(frames) == 2


class TestReadGameTicks:
    """Tests for read_game_ticks function."""

    def test_read_game_state_file(self, tmp_path):
        """Test reading game_state.jsonl."""
        game_state = tmp_path / "game_state.jsonl"
        game_state.write_text(
            json.dumps({"tick_id": 1, "timestamp_ms": 1000}) + "\n"
            + json.dumps({"tick_id": 2, "timestamp_ms": 2000}) + "\n"
        )

        ticks = read_game_ticks(tmp_path)
        assert len(ticks) == 2
        assert ticks[0] == (1, 1000)
        assert ticks[1] == (2, 2000)

    def test_read_sorted_by_timestamp(self, tmp_path):
        """Test ticks are sorted by timestamp_ms."""
        game_state = tmp_path / "game_state.jsonl"
        game_state.write_text(
            json.dumps({"tick_id": 3, "timestamp_ms": 3000}) + "\n"
            + json.dumps({"tick_id": 1, "timestamp_ms": 1000}) + "\n"
            + json.dumps({"tick_id": 2, "timestamp_ms": 2000}) + "\n"
        )

        ticks = read_game_ticks(tmp_path)
        assert ticks[0][1] == 1000
        assert ticks[1][1] == 2000
        assert ticks[2][1] == 3000

    def test_missing_file_raises(self, tmp_path):
        """Test that missing game_state.jsonl raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            read_game_ticks(tmp_path)


class TestCalculateGaps:
    """Tests for calculate_gaps function."""

    def test_all_within_10ms(self):
        """Test when all frames are within 10ms of a tick."""
        frames = [(1, 10000000), (2, 20000000), (3, 30000000)]  # 10ms, 20ms, 30ms
        ticks = [(1, 10), (2, 20), (3, 30)]  # 10ms, 20ms, 30ms

        le_10ms, le_50ms, le_100ms, gt_100ms = calculate_gaps(frames, ticks)

        assert le_10ms == 3
        assert le_50ms == 0
        assert le_100ms == 0
        assert gt_100ms == 0

    def test_gaps_in_different_buckets(self):
        """Test frames fall into different gap buckets."""
        # The algorithm finds closest tick in either direction.
        # Tick timestamps: 10, 80, 160, 500ms
        # - Frame at 15ms: closest 10ms -> gap 5ms -> <=10ms
        # - Frame at 65ms: closest 80ms -> gap 15ms -> <=50ms
        # - Frame at 140ms: closest 160ms -> gap 20ms -> <=50ms
        # - Frame at 300ms: closest 160ms (not 500ms!) -> gap 140ms -> >100ms
        frames = [
            (1, 15000000),    # 15ms -> 5ms gap to 10ms tick -> <=10ms
            (2, 65000000),    # 65ms -> 15ms gap to 80ms tick -> <=50ms
            (3, 140000000),   # 140ms -> 20ms gap to 160ms tick -> <=50ms
            (4, 300000000),   # 300ms -> 140ms gap to 160ms tick -> >100ms
        ]
        ticks = [(1, 10), (2, 80), (3, 160), (4, 500)]

        le_10ms, le_50ms, le_100ms, gt_100ms = calculate_gaps(frames, ticks)

        assert le_10ms == 1
        assert le_50ms == 2  # 65ms and 140ms are both <=50ms from closest tick
        assert le_100ms == 0
        assert gt_100ms == 1

    def test_empty_frames(self):
        """Test with empty frames list."""
        frames = []
        ticks = [(1, 10), (2, 20)]

        le_10ms, le_50ms, le_100ms, gt_100ms = calculate_gaps(frames, ticks)

        assert le_10ms == 0
        assert le_50ms == 0
        assert le_100ms == 0
        assert gt_100ms == 0


class TestCalculateRatios:
    """Tests for calculate_ratios function."""

    def test_all_strict(self):
        """Test when all frames are in strict bucket."""
        ratio_strict, ratio_ok, ratio_tolerable = calculate_ratios(100, 100, 0, 0)

        assert ratio_strict == 1.0
        assert ratio_ok == 1.0
        assert ratio_tolerable == 1.0

    def test_zero_frames(self):
        """Test with zero total frames."""
        ratio_strict, ratio_ok, ratio_tolerable = calculate_ratios(0, 0, 0, 0)

        assert ratio_strict == 0.0
        assert ratio_ok == 0.0
        assert ratio_tolerable == 0.0

    def test_partial_distribution(self):
        """Test partial distribution."""
        # 50 in strict, 30 in ok (10-50ms), 15 in tolerable (50-100ms), 5 >100ms
        ratio_strict, ratio_ok, ratio_tolerable = calculate_ratios(100, 50, 30, 15)

        assert ratio_strict == 0.5
        assert ratio_ok == 0.8  # 50 + 30
        assert ratio_tolerable == 0.95  # 50 + 30 + 15


class TestDetermineVerdict:
    """Tests for determine_verdict function."""

    def test_pass_strict(self):
        """Test PASS_STRICT verdict."""
        assert determine_verdict(0.80, 0.0, 0.0) == "PASS_STRICT"
        assert determine_verdict(1.0, 0.0, 0.0) == "PASS_STRICT"

    def test_pass_ok(self):
        """Test PASS_OK verdict."""
        assert determine_verdict(0.79, 0.95, 0.0) == "PASS_OK"
        assert determine_verdict(0.50, 1.0, 0.0) == "PASS_OK"

    def test_pass_tolerable(self):
        """Test PASS_TOLERABLE verdict."""
        assert determine_verdict(0.0, 0.94, 0.99) == "PASS_TOLERABLE"
        assert determine_verdict(0.0, 0.50, 1.0) == "PASS_TOLERABLE"

    def test_fail(self):
        """Test FAIL verdict."""
        assert determine_verdict(0.0, 0.0, 0.98) == "FAIL"
        assert determine_verdict(0.0, 0.0, 0.0) == "FAIL"


class TestFormatHumanReadable:
    """Tests for format_human_readable function."""

    def test_format_output(self):
        """Test human-readable output formatting."""
        output = format_human_readable(
            session_id="test_session",
            total_frames=100,
            total_ticks=100,
            le_10ms=80,
            le_50ms=15,
            le_100ms=5,
            gt_100ms=0,
            ratio_strict=0.80,
            ratio_ok=0.95,
            ratio_tolerable=1.0,
            verdict="PASS_STRICT",
        )

        assert "test_session" in output
        assert "camera frames: 100" in output
        assert "game ticks:    100" in output
        assert "PASS_STRICT" in output
        assert "80.0% within 10ms" in output


class TestFormatJsonOutput:
    """Tests for format_json_output function."""

    def test_format_json(self):
        """Test JSON output formatting."""
        output = format_json_output(
            total_frames=100,
            total_ticks=100,
            le_10ms=80,
            le_50ms=15,
            le_100ms=5,
            gt_100ms=0,
            ratio_strict=0.80,
            ratio_ok=0.95,
            ratio_tolerable=1.0,
            verdict="PASS_STRICT",
        )

        result = json.loads(output)
        assert result["camera_frames"] == 100
        assert result["game_ticks"] == 100
        assert result["le_10ms"] == 80
        assert result["le_50ms"] == 15
        assert result["le_100ms"] == 5
        assert result["gt_100ms"] == 0
        assert result["verdict"] == "PASS_STRICT"
