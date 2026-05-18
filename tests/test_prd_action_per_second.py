#!/usr/bin/env python3
"""Tests for bin/prd_test_action_per_second.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_action_per_second.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _import_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("action_per_second", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

def test_script_exists():
    assert SCRIPT.exists()


def test_help():
    result = _run(["--help"])
    assert result.returncode == 0
    assert "actions-per-second" in result.stdout.lower() or "action" in result.stdout.lower()


def test_no_args_returns_1():
    result = _run([])
    assert result.returncode == 1


def test_cli_direct_actions_pass():
    result = _run(["-a", "1.0", "2.0", "3.0"])
    assert result.returncode == 0
    assert "PASSED" in result.stdout


def test_cli_direct_actions_fail():
    result = _run(["-a", "0.01", "0.02", "0.03"])
    assert result.returncode == 1
    assert "FAILED" in result.stdout


def test_cli_json_output():
    result = _run(["-a", "1.0", "2.0", "3.0", "-j"])
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["quality_status"] == "acceptable"
    assert data["in_range"] is True


def test_cli_missing_file():
    result = _run(["-i", "/tmp/does_not_exist_action.json"])
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------

class TestCalculateMedian:
    def test_basic_median(self):
        mod = _import_module()
        assert mod.calculate_median_actions_per_second([1.0, 2.0, 3.0]) == 2.0

    def test_even_count(self):
        mod = _import_module()
        assert mod.calculate_median_actions_per_second([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_single_value(self):
        mod = _import_module()
        assert mod.calculate_median_actions_per_second([3.5]) == 3.5

    def test_empty_raises(self):
        mod = _import_module()
        with pytest.raises(ValueError, match="empty"):
            mod.calculate_median_actions_per_second([])


class TestQualityAcceptable:
    def test_within_range(self):
        mod = _import_module()
        assert mod.is_quality_acceptable(1.0) is True
        assert mod.is_quality_acceptable(0.5) is True
        assert mod.is_quality_acceptable(5.0) is True

    def test_below_range(self):
        mod = _import_module()
        assert mod.is_quality_acceptable(0.4) is False
        assert mod.is_quality_acceptable(0.1) is False

    def test_above_range(self):
        mod = _import_module()
        assert mod.is_quality_acceptable(5.1) is False
        assert mod.is_quality_acceptable(10.0) is False


class TestAnalyzeCaptureQuality:
    def test_acceptable_capture(self):
        mod = _import_module()
        result = mod.analyze_capture_quality([1.0, 2.0, 3.0])
        assert result["quality_status"] == "acceptable"
        assert result["in_range"] is True
        assert result["sample_count"] == 3

    def test_low_quality_capture(self):
        mod = _import_module()
        result = mod.analyze_capture_quality([0.1, 0.2, 0.3])
        assert result["quality_status"] == "low-quality"
        assert result["in_range"] is False

    def test_min_max_reported(self):
        mod = _import_module()
        result = mod.analyze_capture_quality([1.0, 5.0, 3.0])
        assert result["min_actions_per_second"] == 1.0
        assert result["max_actions_per_second"] == 5.0


class TestLoadActionsFromFile:
    def test_json_list_of_numbers(self):
        mod = _import_module()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([1.0, 2.0, 3.0], f)
            f.flush()
            actions = mod.load_actions_from_file(Path(f.name))
        assert actions == [1.0, 2.0, 3.0]

    def test_plain_text(self):
        mod = _import_module()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("1.0\n2.0\n3.0\n")
            f.flush()
            actions = mod.load_actions_from_file(Path(f.name))
        assert actions == [1.0, 2.0, 3.0]

    def test_file_not_found(self):
        mod = _import_module()
        with pytest.raises(FileNotFoundError):
            mod.load_actions_from_file(Path("/tmp/nonexistent_action_file.json"))

    def test_camera_data_dict_returns_empty(self):
        mod = _import_module()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"camera_position": [0, 0, 0], "world_cube_radius": 10}, f)
            f.flush()
            actions = mod.load_actions_from_file(Path(f.name))
        assert actions == []

    def test_action_camera_format(self):
        mod = _import_module()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            # 10 records, 0.5s apart => 2 actions/sec
            records = [{"timestamp": i * 0.5} for i in range(10)]
            json.dump(records, f)
            f.flush()
            actions = mod.load_actions_from_file(Path(f.name))
        assert len(actions) == 9  # 10 records => 9 deltas
        # Each delta is 0.5s, so rate = 1/0.5 = 2.0
        assert all(abs(a - 2.0) < 0.001 for a in actions)

    def test_action_camera_with_time_field(self):
        mod = _import_module()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            records = [{"time": i * 1.0} for i in range(5)]
            json.dump(records, f)
            f.flush()
            actions = mod.load_actions_from_file(Path(f.name))
        assert len(actions) == 4
        # 1s apart => 1.0 action/sec
        assert all(abs(a - 1.0) < 0.001 for a in actions)


class TestHasTimestampField:
    def test_timestamp_present(self):
        mod = _import_module()
        assert mod._has_timestamp_field([{"timestamp": 1.0}]) is True

    def test_time_present(self):
        mod = _import_module()
        assert mod._has_timestamp_field([{"time": 1.0}]) is True

    def test_frame_present(self):
        mod = _import_module()
        assert mod._has_timestamp_field([{"frame": 1}]) is True

    def test_no_timestamp(self):
        mod = _import_module()
        assert mod._has_timestamp_field([{"foo": "bar"}]) is False

    def test_empty_list(self):
        mod = _import_module()
        assert mod._has_timestamp_field([]) is False


class TestIsCameraDataDict:
    def test_camera_data(self):
        mod = _import_module()
        assert mod._is_camera_data_dict({"camera_position": [0, 0, 0]}) is True
        assert mod._is_camera_data_dict({"intrinsics": {}}) is True
        assert mod._is_camera_data_dict({"world_cube_radius": 10}) is True

    def test_not_camera_data(self):
        mod = _import_module()
        assert mod._is_camera_data_dict({"timestamp": 1.0}) is False
        assert mod._is_camera_data_dict({"action": "jump"}) is False
