#!/usr/bin/env python3
"""
Tests for bin/data_quality_report.py — buyer data quality analyzer.

Covers: magnitude, report (stationary %, WASD distribution, route distribution,
frame continuity, camera speed mean, status), main CLI (--buyer-dir happy path,
missing file, invalid JSON, non-list data, PASS exit 0, FAIL exit 1,
exception exit 1).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import bin.data_quality_report as data_quality_report


class TestMagnitude:
    """Tests for the magnitude() helper."""

    def test_zero_vector(self):
        """Magnitude of [0,0,0] is 0."""
        assert data_quality_report.magnitude([0, 0, 0]) == 0.0

    def test_unit_vector(self):
        """Magnitude of [1,0,0] is 1."""
        assert data_quality_report.magnitude([1, 0, 0]) == 1.0

    def test_3_4_5_triangle(self):
        """Magnitude of [3,4,0] is 5 (Pythagoras)."""
        assert data_quality_report.magnitude([3, 4, 0]) == 5.0

    def test_general_vector(self):
        """Magnitude of [2,3,6] is sqrt(4+9+36) = 7."""
        assert data_quality_report.magnitude([2, 3, 6]) == 7.0

    def test_empty_list(self):
        """Empty list → 0."""
        assert data_quality_report.magnitude([]) == 0.0

    def test_short_vector(self):
        """Vector with <3 components → 0."""
        assert data_quality_report.magnitude([1, 2]) == 0.0


class TestReportMissingFile:
    """Tests for report() when action_camera.json is missing."""

    def test_missing_file_raises(self):
        """report() raises FileNotFoundError if action_camera.json is absent."""
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(FileNotFoundError):
                data_quality_report.report(tmp)

    def test_missing_file_error_message_contains_dir(self):
        """Error message references the buyer directory path."""
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(FileNotFoundError) as exc_info:
                data_quality_report.report(tmp)
            assert tmp in str(exc_info.value)


class TestReportEmpty:
    """Tests for report() with an empty list."""

    def test_empty_list_returns_zero_record_metrics(self):
        """Empty list → 0 records, 0% stationary, mean 0."""
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "action_camera.json"
            json_path.write_text("[]")
            result = data_quality_report.report(tmp)
            assert result["total_records"] == 0
            assert result["pct_stationary"] == 0
            assert result["camera_speed_mean_magnitude"] == 0.0

    def test_empty_list_frame_continuity_pass(self):
        """Empty list → frame_continuity PASS (no gaps possible)."""
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "action_camera.json"
            json_path.write_text("[]")
            result = data_quality_report.report(tmp)
            assert result["frame_continuity"] == "PASS"
            assert result["status"] == "PASS"


class TestReportStationary:
    """Tests for stationary percentage calculation."""

    def test_all_stationary(self):
        """All records with zero speed → 100% stationary."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [
                {"player_speed": [0, 0, 0]},
                {"player_speed": [0, 0, 0]},
            ]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["pct_stationary"] == 100.0

    def test_all_moving(self):
        """All records moving → 0% stationary."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [
                {"player_speed": [1, 0, 0]},
                {"player_speed": [0, 1, 0]},
            ]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["pct_stationary"] == 0.0

    def test_mixed_stationary(self):
        """50/50 split → 50% stationary."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [
                {"player_speed": [0, 0, 0]},
                {"player_speed": [1, 0, 0]},
            ]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["pct_stationary"] == 50.0

    def test_missing_player_speed_defaults_to_zero(self):
        """Missing player_speed → treated as stationary (0 magnitude)."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1}]  # no player_speed
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["pct_stationary"] == 100.0


class TestReportWASD:
    """Tests for WASD key distribution."""

    def test_w_only(self):
        """W key pressed once → {'w': 1}."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"keys": {"w": True}}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["wasd_distribution"] == {"w": 1}

    def test_all_keys(self):
        """All WASD keys pressed → all counted."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [
                {
                    "keys": {
                        "w": True,
                        "a": True,
                        "s": True,
                        "d": True,
                    }
                }
            ]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["wasd_distribution"] == {"w": 1, "a": 1, "s": 1, "d": 1}

    def test_no_keys(self):
        """No WASD keys → empty distribution."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"keys": {}}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["wasd_distribution"] == {}

    def test_missing_keys_field(self):
        """Missing keys field → no error, empty distribution."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["wasd_distribution"] == {}

    def test_repeated_key_counts(self):
        """W key across multiple records → count accumulates."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [
                {"keys": {"w": True}},
                {"keys": {"w": True}},
                {"keys": {"w": True}},
            ]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["wasd_distribution"] == {"w": 3}


class TestReportRouteType:
    """Tests for route_type distribution."""

    def test_single_route(self):
        """All records same route → single entry."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"route_type": "highway"}, {"route_type": "highway"}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["route_type_distribution"] == {"highway": 2}

    def test_multiple_routes(self):
        """Multiple route types → count each."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [
                {"route_type": "highway"},
                {"route_type": "city"},
                {"route_type": "highway"},
            ]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["route_type_distribution"] == {"highway": 2, "city": 1}

    def test_missing_route_type_defaults_to_unknown(self):
        """Missing route_type → 'unknown' bucket."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["route_type_distribution"] == {"unknown": 1}


class TestReportFrameContinuity:
    """Tests for frame continuity check."""

    def test_sequential_frames_pass(self):
        """Consecutive frame_ids → PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1}, {"frame_id": 2}, {"frame_id": 3}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["frame_continuity"] == "PASS"
            assert result["status"] == "PASS"

    def test_gap_in_frames_fails(self):
        """Gap in frame_ids → FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1}, {"frame_id": 3}]  # missing 2
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["frame_continuity"] == "FAIL"
            assert result["status"] == "FAIL"

    def test_no_frame_id_field_ignored(self):
        """Records without frame_id → ignored for continuity check."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1}, {"foo": "bar"}, {"frame_id": 2}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["frame_continuity"] == "PASS"


class TestReportCameraSpeed:
    """Tests for camera speed mean magnitude."""

    def test_known_magnitudes(self):
        """Two records with camera_speed [3,4,0] and [0,0,0] → mean 2.5."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [
                {"camera_speed": [3, 4, 0]},  # magnitude 5
                {"camera_speed": [0, 0, 0]},  # magnitude 0
            ]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["camera_speed_mean_magnitude"] == 2.5

    def test_missing_camera_speed_zero(self):
        """Missing camera_speed → treated as 0."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            result = data_quality_report.report(tmp)
            assert result["camera_speed_mean_magnitude"] == 0.0


class TestReportInvalidData:
    """Tests for report() with invalid data shapes."""

    def test_non_list_data_raises(self):
        """action_camera.json containing a dict (not list) → ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "action_camera.json").write_text("{}")
            with pytest.raises(ValueError):
                data_quality_report.report(tmp)

    def test_invalid_json_raises(self):
        """Malformed JSON → JSONDecodeError."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "action_camera.json").write_text("not json")
            with pytest.raises(json.JSONDecodeError):
                data_quality_report.report(tmp)


class TestMainCLI:
    """Tests for the main() CLI entrypoint."""

    def test_pass_exits_zero(self, capsys):
        """PASS data → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1, "player_speed": [1, 0, 0]}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            with patch.object(sys, "argv", ["data_quality_report", "--buyer-dir", tmp]):
                with pytest.raises(SystemExit) as exc_info:
                    data_quality_report.main()
            assert exc_info.value.code == 0

    def test_fail_exits_one(self, capsys):
        """FAIL data (gap in frames) → exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1}, {"frame_id": 3}]  # gap
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            with patch.object(sys, "argv", ["data_quality_report", "--buyer-dir", tmp]):
                with pytest.raises(SystemExit) as exc_info:
                    data_quality_report.main()
            assert exc_info.value.code == 1

    def test_missing_file_exits_one(self, capsys):
        """Missing action_camera.json → exit 1, error to stderr."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sys, "argv", ["data_quality_report", "--buyer-dir", tmp]):
                with pytest.raises(SystemExit) as exc_info:
                    data_quality_report.main()
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "error" in captured.err.lower()

    def test_missing_buyer_dir_arg_exits(self):
        """Missing --buyer-dir → SystemExit from argparse."""
        with patch.object(sys, "argv", ["data_quality_report"]):
            with pytest.raises(SystemExit):
                data_quality_report.main()

    def test_pass_prints_metrics_to_stdout(self, capsys):
        """PASS path prints JSON metrics to stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            data = [{"frame_id": 1}]
            (Path(tmp) / "action_camera.json").write_text(json.dumps(data))
            with patch.object(sys, "argv", ["data_quality_report", "--buyer-dir", tmp]):
                with pytest.raises(SystemExit) as exc_info:
                    data_quality_report.main()
            assert exc_info.value.code == 0
            captured = capsys.readouterr()
            # stdout contains the metrics dict
            assert "total_records" in captured.out
            assert "status" in captured.out
