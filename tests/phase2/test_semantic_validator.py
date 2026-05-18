"""
Tests for semantic_validator.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from semantic_validator import validate_action_camera_semantics

# Path to the CLI script
SEMANTIC_VALIDATOR_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "src" / "oyster_agent_runner" / "phase2" / "semantic_validator.py"
)


def create_valid_record(frame_num: int) -> dict:
    """Create a valid action camera record."""
    # Create distribution: W=40%, A=20%, S=20%, D=20%
    # This meets the tolerance: W in [30-50], A/S/D in [10-30]
    key_distribution = ["W"] * 40 + ["A"] * 20 + ["S"] * 20 + ["D"] * 20
    key_index = frame_num % 100
    key_code = key_distribution[key_index]

    return {
        "frame": frame_num,
        "player_speed": {"vx": 1.0, "vy": 0.0, "vz": 0.0},
        "keyCode": key_code,
        "route_type": "1",
        "oula": {"x": 45.0, "y": -30.0, "z": 90.0},
        "quaternion": [0.707, 0.0, 0.707, 0.0],  # Approximately unit norm
        "camera_intrinsics": {"fx": 800.0, "fy": 800.0},
    }


def test_passes_on_valid_records():
    """Test passes on synthetic 100 records all valid."""
    records = [create_valid_record(i) for i in range(100)]
    result = validate_action_camera_semantics(records)

    assert result["iter_count"] == 100
    assert result["stationary_pct"] == 0.0  # No stationary frames
    assert result["stationary_within_10pct"] == True
    # Check WASD distribution percentages
    w_pct = result["wasd_distribution"]["W"]
    a_pct = result["wasd_distribution"]["A"]
    s_pct = result["wasd_distribution"]["S"]
    d_pct = result["wasd_distribution"]["D"]

    # With our distribution, we should have approximately:
    # W: 40%, A: 20%, S: 20%, D: 20%
    assert 0.35 <= w_pct <= 0.45, f"W percentage {w_pct} not in expected range"
    assert 0.15 <= a_pct <= 0.25, f"A percentage {a_pct} not in expected range"
    assert 0.15 <= s_pct <= 0.25, f"S percentage {s_pct} not in expected range"
    assert 0.15 <= d_pct <= 0.25, f"D percentage {d_pct} not in expected range"

    assert result["wasd_within_tolerance"] == True
    assert result["frame_continuous"] == True
    assert result["rotation_in_range"] == True
    assert result["quaternion_unit_norm"] == True
    assert result["fx_equals_fy"] == True
    assert result["summary_pass"] == True
    assert len(result["issues"]) == 0

    print("✓ test_passes_on_valid_records passed")


def test_fails_on_too_many_stationary():
    """Test fails when 50% of frames are stationary."""
    records = []
    for i in range(100):
        record = create_valid_record(i)
        # Make every other frame stationary
        if i % 2 == 0:
            record["player_speed"] = {"vx": 0.0, "vy": 0.0, "vz": 0.0}
        records.append(record)

    result = validate_action_camera_semantics(records)

    assert result["iter_count"] == 100
    assert abs(result["stationary_pct"] - 50.0) < 1.0  # Approximately 50%
    assert result["stationary_within_10pct"] == False
    assert result["summary_pass"] == False
    assert any("Stationary percentage" in issue for issue in result["issues"])

    print("✓ test_fails_on_too_many_stationary passed")


def test_fails_on_missing_frames():
    """Test fails when frames are not continuous."""
    records = []
    for i in range(10):
        record = create_valid_record(i)
        # Skip frame 5 to create a gap
        if i == 5:
            record["frame"] = 7  # Gap: 4 -> 7
        records.append(record)

    result = validate_action_camera_semantics(records)

    assert result["frame_continuous"] == False
    assert result["summary_pass"] == False
    # The actual error message is "Frame gap detected"
    assert any("Frame gap" in issue for issue in result["issues"])

    print("✓ test_fails_on_missing_frames passed")


def test_fails_on_out_of_range_rotation():
    """Test fails when rotation values are out of range."""
    records = [create_valid_record(i) for i in range(10)]
    # Set an out-of-range rotation value
    records[5]["oula"] = {"x": 200.0, "y": 0.0, "z": 0.0}  # x > 180

    result = validate_action_camera_semantics(records)

    assert result["rotation_in_range"] == False
    assert result["summary_pass"] == False
    # The actual error message is "Rotation out of bounds"
    assert any("Rotation" in issue and "out of" in issue for issue in result["issues"])

    print("✓ test_fails_on_out_of_range_rotation passed")


def test_fails_on_bad_quaternion():
    """Test fails when quaternion is not unit norm."""
    records = [create_valid_record(i) for i in range(10)]
    # Set a non-unit quaternion
    records[5]["quaternion"] = [2.0, 0.0, 0.0, 0.0]  # Norm = 2, not 1

    result = validate_action_camera_semantics(records)

    assert result["quaternion_unit_norm"] == False
    assert result["summary_pass"] == False
    assert any("Quaternion norm" in issue for issue in result["issues"])

    print("✓ test_fails_on_bad_quaternion passed")


def test_cli_valid_file():
    """Test CLI with valid JSON file."""
    # Need 100 records to pass WASD distribution tolerance check
    records = [create_valid_record(i) for i in range(100)]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(records, f)
        temp_file = f.name

    try:
        # Run the CLI with the correct path to the script
        result = subprocess.run(
            [sys.executable, str(SEMANTIC_VALIDATOR_SCRIPT), temp_file],
            capture_output=True,
            text=True,
        )
        # Should exit with 0 for valid records
        assert result.returncode == 0, f"Expected exit code 0, got {result.returncode}. stdout: {result.stdout}, stderr: {result.stderr}"
        print("✓ test_cli_valid_file passed")
    finally:
        os.unlink(temp_file)


def test_cli_invalid_file():
    """Test CLI with invalid JSON file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("invalid json")
        temp_file = f.name

    try:
        # Run the CLI with the correct path to the script
        result = subprocess.run(
            [sys.executable, str(SEMANTIC_VALIDATOR_SCRIPT), temp_file],
            capture_output=True,
            text=True,
        )
        # Should exit with non-zero code for invalid JSON
        assert result.returncode != 0, "Expected non-zero exit code for invalid JSON"
        print("✓ test_cli_invalid_file passed")
    finally:
        os.unlink(temp_file)


def test_edge_cases():
    """Test edge cases."""
    # Empty records
    result = validate_action_camera_semantics([])
    assert result["iter_count"] == 0
    assert result["summary_pass"] == False
    assert "No records provided" in result["issues"]

    # Single record
    records = [create_valid_record(0)]
    result = validate_action_camera_semantics(records)
    assert result["iter_count"] == 1

    # Record missing some fields
    records = [{"frame": 0}]
    result = validate_action_camera_semantics(records)
    # Should handle missing fields gracefully

    print("✓ test_edge_cases passed")