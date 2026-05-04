"""
Tests for semantic_validator.py
"""
import json
import os
import tempfile

from semantic_validator import validate_action_camera_semantics


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
        "camera_intrinsics": {"fx": 800.0, "fy": 800.0}
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


def test_fails_on_skewed_wasd():
    """Test fails when WASD distribution is skewed (all W)."""
    records = []
    for i in range(100):
        record = create_valid_record(i)
        # All W keys
        record["keyCode"] = "W"
        records.append(record)

    result = validate_action_camera_semantics(records)

    assert result["iter_count"] == 100
    assert result["wasd_distribution"]["W"] == 1.0
    assert result["wasd_distribution"]["A"] == 0.0
    assert result["wasd_distribution"]["S"] == 0.0
    assert result["wasd_distribution"]["D"] == 0.0
    assert result["wasd_within_tolerance"] == False
    assert result["summary_pass"] == False
    assert any("WASD distribution out of tolerance" in issue for issue in result["issues"])

    print("✓ test_fails_on_skewed_wasd passed")


def test_fails_on_frame_gap():
    """Test fails when there's a frame gap."""
    records = []
    for i in range(100):
        if i == 50:
            continue  # Skip frame 50 to create a gap
        record = create_valid_record(i)
        records.append(record)

    result = validate_action_camera_semantics(records)

    assert result["iter_count"] == 99
    assert result["frame_continuous"] == False
    assert result["summary_pass"] == False
    assert any("Frame gap detected" in issue for issue in result["issues"])

    print("✓ test_fails_on_frame_gap passed")


def test_fails_on_bad_quaternion():
    """Test fails when quaternion norm is not unit."""
    records = []
    for i in range(100):
        record = create_valid_record(i)
        # Bad quaternion with norm != 1
        record["quaternion"] = [10.0, 0.0, 0.0, 0.0]
        records.append(record)

    result = validate_action_camera_semantics(records)

    assert result["iter_count"] == 100
    assert result["quaternion_unit_norm"] == False
    assert result["summary_pass"] == False
    assert any("Quaternion norm" in issue for issue in result["issues"])

    print("✓ test_fails_on_bad_quaternion passed")


def test_cli_valid_file():
    """Test CLI with valid JSON file."""
    records = [create_valid_record(i) for i in range(10)]

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(records, f)
        temp_file = f.name

    try:
        # Run the CLI
        os.system(f"python3 semantic_validator.py {temp_file} > /dev/null 2>&1")
        # Check exit code (should be 0 for valid)
        exit_code = os.system(f"python3 semantic_validator.py {temp_file} > /dev/null 2>&1")
        assert exit_code == 0, f"Expected exit code 0, got {exit_code}"
        print("✓ test_cli_valid_file passed")
    finally:
        os.unlink(temp_file)


def test_cli_invalid_file():
    """Test CLI with invalid JSON file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("invalid json")
        temp_file = f.name

    try:
        # Run the CLI
        exit_code = os.system(f"python3 semantic_validator.py {temp_file} > /dev/null 2>&1")
        # Should exit with non-zero code
        assert exit_code != 0, "Expected non-zero exit code for invalid JSON"
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


def test_rotation_out_of_range():
    """Test fails when rotation is out of range."""
    records = []
    for i in range(10):
        record = create_valid_record(i)
        # Rotation out of range
        record["oula"] = {"x": 200.0, "y": -200.0, "z": 0.0}
        records.append(record)

    result = validate_action_camera_semantics(records)

    assert result["rotation_in_range"] == False
    assert result["summary_pass"] == False
    assert any("Rotation" in issue and "out of range" in issue for issue in result["issues"])

    print("✓ test_rotation_out_of_range passed")


def test_fx_not_equals_fy():
    """Test fails when fx != fy."""
    records = []
    for i in range(10):
        record = create_valid_record(i)
        # Different fx and fy
        record["camera_intrinsics"] = {"fx": 800.0, "fy": 600.0}
        records.append(record)

    result = validate_action_camera_semantics(records)

    assert result["fx_equals_fy"] == False
    assert result["summary_pass"] == False
    assert any("Camera intrinsics fx" in issue for issue in result["issues"])

    print("✓ test_fx_not_equals_fy passed")


if __name__ == "__main__":
    # Run all tests
    test_passes_on_valid_records()
    test_fails_on_too_many_stationary()
    test_fails_on_skewed_wasd()
    test_fails_on_frame_gap()
    test_fails_on_bad_quaternion()
    test_cli_valid_file()
    test_cli_invalid_file()
    test_edge_cases()
    test_rotation_out_of_range()
    test_fx_not_equals_fy()

    print("\n✅ All tests passed!")
