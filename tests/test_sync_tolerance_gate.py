#!/usr/bin/env python3
"""
Tests for sync_tolerance_gate.py
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


def create_test_session(tmp_path: Path) -> Path:
    """Create a test session with 5 ticks and 10 camera frames."""
    session_dir = tmp_path / "test_session"
    session_dir.mkdir()

    # Create game_state.jsonl with 5 ticks at 50ms intervals
    game_state_lines = []
    for i in range(5):
        tick_data = {"tick_id": i, "timestamp_ms": i * 50.0}  # 0, 50, 100, 150, 200 ms
        game_state_lines.append(json.dumps(tick_data))

    game_state_file = session_dir / "game_state.jsonl"
    game_state_file.write_text("\n".join(game_state_lines))

    # Create action_camera_1.jsonl with 10 frames at ~16.67ms intervals
    camera_lines = []
    for i in range(10):
        frame_data = {
            "frame_id": i,
            "timestamp_ns": i * 16_667_000,  # ~16.67ms intervals in ns
        }
        camera_lines.append(json.dumps(frame_data))

    camera_file = session_dir / "action_camera_1.jsonl"
    camera_file.write_text("\n".join(camera_lines))

    return session_dir


def test_basic_functionality(tmp_path):
    """Test basic functionality with fixture data."""
    session_dir = create_test_session(tmp_path)

    # Run the script
    result = subprocess.run(
        [sys.executable, "bin/sync_tolerance_gate.py", str(session_dir)],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    # Check exit code
    assert result.returncode == 0, f"Script failed with output:\n{result.stdout}\n{result.stderr}"

    # Check output contains expected information
    assert "SYNC TOLERANCE AUDIT" in result.stdout
    assert "camera frames: 10" in result.stdout
    assert "game ticks:    5" in result.stdout
    assert "Alignment gap distribution:" in result.stdout

    # Check for verdict (should be PASS_STRICT based on our data)
    assert "Verdict:" in result.stdout


def test_json_output(tmp_path):
    """Test JSON output format."""
    session_dir = create_test_session(tmp_path)

    # Run the script with --json flag
    result = subprocess.run(
        [sys.executable, "bin/sync_tolerance_gate.py", str(session_dir), "--json"],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    # Check exit code
    assert result.returncode == 0, f"Script failed with output:\n{result.stdout}\n{result.stderr}"

    # Parse JSON output
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError as e:
        pytest.fail(f"Failed to parse JSON output: {e}\nOutput: {result.stdout}")

    # Check required keys
    required_keys = [
        "camera_frames",
        "game_ticks",
        "le_10ms",
        "le_50ms",
        "le_100ms",
        "gt_100ms",
        "ratio_strict",
        "ratio_ok",
        "ratio_tolerable",
        "verdict",
    ]

    for key in required_keys:
        assert key in data, f"Missing key in JSON output: {key}"

    # Check specific values
    assert data["camera_frames"] == 10
    assert data["game_ticks"] == 5
    assert data["verdict"] in ["PASS_STRICT", "PASS_OK", "PASS_TOLERABLE", "FAIL"]

    # Check ratios are between 0 and 1
    assert 0 <= data["ratio_strict"] <= 1
    assert 0 <= data["ratio_ok"] <= 1
    assert 0 <= data["ratio_tolerable"] <= 1

    # Check counts sum to total frames
    total_counted = data["le_10ms"] + data["le_50ms"] + data["le_100ms"] + data["gt_100ms"]
    assert total_counted == data["camera_frames"]


def test_empty_session_dir(tmp_path):
    """Test handling of empty session directory."""
    empty_dir = tmp_path / "empty_session"
    empty_dir.mkdir()

    result = subprocess.run(
        [sys.executable, "bin/sync_tolerance_gate.py", str(empty_dir)],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    # Should fail with error message
    assert result.returncode != 0
    assert "Error:" in result.stderr or "Error:" in result.stdout
    assert "No camera frames" in result.stderr or "No camera frames" in result.stdout


def test_no_game_state(tmp_path):
    """Test handling of session with camera files but no game_state.jsonl."""
    session_dir = tmp_path / "no_game_state"
    session_dir.mkdir()

    # Create only camera file
    camera_file = session_dir / "action_camera_1.jsonl"
    camera_file.write_text(json.dumps({"frame_id": 0, "timestamp_ns": 0}))

    result = subprocess.run(
        [sys.executable, "bin/sync_tolerance_gate.py", str(session_dir)],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    # Should fail with error about missing game_state.jsonl
    assert result.returncode != 0
    assert "Error:" in result.stderr or "Error:" in result.stdout
    assert "game_state.jsonl" in result.stderr or "game_state.jsonl" in result.stdout


def test_multiple_camera_files(tmp_path):
    """Test handling of multiple camera files."""
    session_dir = tmp_path / "multi_camera"
    session_dir.mkdir()

    # Create game_state.jsonl
    game_state_file = session_dir / "game_state.jsonl"
    game_state_file.write_text(
        "\n".join(
            [
                json.dumps({"tick_id": 0, "timestamp_ms": 0}),
                json.dumps({"tick_id": 1, "timestamp_ms": 50}),
            ]
        )
    )

    # Create multiple camera files
    camera1_file = session_dir / "action_camera_1.jsonl"
    camera1_file.write_text(
        "\n".join(
            [
                json.dumps({"frame_id": 0, "timestamp_ns": 0}),
                json.dumps({"frame_id": 1, "timestamp_ns": 16_667_000}),
            ]
        )
    )

    camera2_file = session_dir / "action_camera_2.jsonl"
    camera2_file.write_text(
        "\n".join(
            [
                json.dumps({"frame_id": 2, "timestamp_ns": 33_334_000}),
                json.dumps({"frame_id": 3, "timestamp_ns": 50_001_000}),
            ]
        )
    )

    result = subprocess.run(
        [sys.executable, "bin/sync_tolerance_gate.py", str(session_dir)],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    # Should succeed
    assert result.returncode == 0
    assert "camera frames: 4" in result.stdout


def test_verdict_calculation():
    """Test verdict calculation logic directly."""
    # Import the functions we need to test
    sys.path.insert(0, str(Path.cwd() / "bin"))
    try:
        from sync_tolerance_gate import determine_verdict

        # Test PASS_STRICT
        assert determine_verdict(0.85, 0.90, 0.95) == "PASS_STRICT"  # ratio_strict >= 0.80
        assert determine_verdict(0.80, 0.80, 0.80) == "PASS_STRICT"  # exactly 0.80

        # Test PASS_OK (when strict < 0.80 but ok >= 0.95)
        assert determine_verdict(0.79, 0.96, 0.97) == "PASS_OK"
        assert determine_verdict(0.70, 0.95, 0.96) == "PASS_OK"  # exactly 0.95

        # Test PASS_TOLERABLE (when strict < 0.80, ok < 0.95, tolerable >= 0.99)
        assert determine_verdict(0.70, 0.94, 0.99) == "PASS_TOLERABLE"
        assert determine_verdict(0.60, 0.80, 0.99) == "PASS_TOLERABLE"  # exactly 0.99

        # Test FAIL (when none of the conditions are met)
        assert determine_verdict(0.70, 0.90, 0.98) == "FAIL"
        assert determine_verdict(0.10, 0.20, 0.30) == "FAIL"

    finally:
        sys.path.pop(0)


def test_gap_calculation():
    """Test gap calculation logic directly."""
    sys.path.insert(0, str(Path.cwd() / "bin"))
    try:
        from sync_tolerance_gate import calculate_gaps

        # Create test data
        frames = [
            (0, 0),  # 0ms - exact match with tick 0
            (1, 5_000_000),  # 5ms - close to tick 0 (5ms gap)
            (2, 12_000_000),  # 12ms - close to tick 0 (12ms gap)
            (3, 48_000_000),  # 48ms - close to tick 1 (2ms gap)
            (4, 55_000_000),  # 55ms - close to tick 1 (5ms gap)
            (5, 120_000_000),  # 120ms - close to tick 2 (20ms gap)
            (6, 300_000_000),  # 300ms - far from any tick (100ms gap from tick 4)
        ]

        ticks = [
            (0, 0.0),  # 0ms
            (1, 50.0),  # 50ms
            (2, 100.0),  # 100ms
            (3, 150.0),  # 150ms
            (4, 200.0),  # 200ms
        ]

        le_10ms, le_50ms, le_100ms, gt_100ms = calculate_gaps(frames, ticks)

        # Expected:
        # frame 0: 0ms gap -> le_10ms
        # frame 1: 5ms gap -> le_10ms
        # frame 2: 12ms gap -> le_50ms (12 <= 50)
        # frame 3: 2ms gap -> le_10ms
        # frame 4: 5ms gap -> le_10ms
        # frame 5: 20ms gap -> le_50ms (20 <= 50)
        # frame 6: 100ms gap -> le_100ms (100 <= 100)

        assert le_10ms == 4  # frames 0, 1, 3, 4
        assert le_50ms == 2  # frames 2, 5
        assert le_100ms == 1  # frame 6
        assert gt_100ms == 0

    finally:
        sys.path.pop(0)


def test_edge_cases(tmp_path):
    """Test edge cases."""
    session_dir = tmp_path / "edge_case"
    session_dir.mkdir()

    # Test with single tick and single frame
    game_state_file = session_dir / "game_state.jsonl"
    game_state_file.write_text(json.dumps({"tick_id": 0, "timestamp_ms": 100.0}))

    camera_file = session_dir / "action_camera_1.jsonl"
    camera_file.write_text(json.dumps({"frame_id": 0, "timestamp_ns": 100_000_000}))  # 100ms

    result = subprocess.run(
        [sys.executable, "bin/sync_tolerance_gate.py", str(session_dir)],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    # Should succeed with 0ms gap
    assert result.returncode == 0
    assert "camera frames: 1" in result.stdout
    assert "game ticks:    1" in result.stdout


def test_malformed_json(tmp_path):
    """Test handling of malformed JSON files."""
    session_dir = tmp_path / "malformed"
    session_dir.mkdir()

    # Create valid game state
    game_state_file = session_dir / "game_state.jsonl"
    game_state_file.write_text(json.dumps({"tick_id": 0, "timestamp_ms": 0}))

    # Create malformed camera file
    camera_file = session_dir / "action_camera_1.jsonl"
    camera_file.write_text('not valid json\n{"frame_id": 0}\n')

    result = subprocess.run(
        [sys.executable, "bin/sync_tolerance_gate.py", str(session_dir)],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    # Should still work (skip malformed lines with warning)
    assert result.returncode == 0 or "Warning:" in result.stderr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
