#!/usr/bin/env python3
"""Tests for bin/verify_round_trip.py — round-trip data integrity checks."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from bin.verify_action_camera import euler_zyx_to_quat  # noqa: E402,I001
from bin.verify_round_trip import (  # noqa: E402,I001
    check1_keyboard,
    check2_mouse_position,
    check3_quat_euler,
    check4_frame_time,
    load_records,
    main,
    reconstruct_key_events,
    replay_key_events,
    run_all_checks,
)


# ---- Helpers ---------------------------------------------------------------

def _build_clean_records(n: int = 30, fps: float = 30.0) -> list[dict[str, Any]]:
    """Synthesize a perfectly self-consistent action_camera record list.

    Static identity quaternion, no mouse motion, no key presses, monotonic
    frame index and time. All four checks should pass.
    """
    records: list[dict[str, Any]] = []
    for i in range(n):
        records.append({
            "frame": i,
            "frame_index": i,
            "timestamp": round(i / fps, 6),
            "fps": fps,
            "mouse_x": 0.5,
            "mouse_y": 0.5,
            "mouse_dx": 0.0,
            "mouse_dy": 0.0,
            "keyCode": [],
            "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
            "camera_rotation_euler": [0.0, 0.0, 0.0],
        })
    return records


def _write_clip(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    clip = tmp_path / "clip"
    clip.mkdir()
    (clip / "action_camera.json").write_text(json.dumps(records))
    return clip


# ---- Check 1: Keyboard event reconstruction --------------------------------

class TestKeyboardReconstruction:
    def test_no_keys_held_passes(self) -> None:
        records = _build_clean_records(20)
        result = check1_keyboard(records)
        assert result["passed"] is True
        assert result["mismatches"] == 0
        assert result["events_synthesized"] == 0

    def test_held_key_diff_yields_correct_events(self) -> None:
        # W (kc=87) pressed at frame 5, released at frame 10.
        records = _build_clean_records(15)
        for i in range(5, 10):
            records[i]["keyCode"] = [87]
        events = reconstruct_key_events(records)
        # Expect 1 down at frame 5 and 1 up at frame 10.
        assert (5, "key_down", 87) in events
        assert (10, "key_up", 87) in events
        assert len(events) == 2

    def test_replay_matches_original_for_complex_pattern(self) -> None:
        records = _build_clean_records(20)
        # A (65) held frames 2-8. Shift (16) held frames 5-12. Both held 5-8.
        for i in range(2, 8):
            records[i]["keyCode"] = sorted(records[i]["keyCode"] + [65])
        for i in range(5, 12):
            records[i]["keyCode"] = sorted(records[i]["keyCode"] + [16])
        result = check1_keyboard(records)
        assert result["passed"] is True
        assert result["mismatches"] == 0

    def test_replay_function_independently(self) -> None:
        events = [(0, "key_down", 87), (3, "key_up", 87)]
        replayed = replay_key_events(events, 6)
        assert replayed[0] == [87]
        assert replayed[1] == [87]
        assert replayed[2] == [87]
        assert replayed[3] == []
        assert replayed[4] == []


# ---- Check 2: Mouse position reconstruction --------------------------------

class TestMouseReconstruction:
    def test_zero_motion_passes(self) -> None:
        records = _build_clean_records(20)
        result = check2_mouse_position(records)
        assert result["passed"] is True
        assert result["mismatches"] == 0

    def test_cumulative_normalized_deltas_pass(self) -> None:
        records = _build_clean_records(10)
        # Move mouse 0.01 normalized per frame. Position must update accordingly.
        for i in range(1, 10):
            records[i]["mouse_x"] = round(0.5 + 0.01 * i, 6)
            records[i]["mouse_dx"] = 0.01
        result = check2_mouse_position(records)
        assert result["passed"] is True

    def test_corrupt_position_caught(self) -> None:
        records = _build_clean_records(10)
        # Position jumps but deltas claim no motion → must fail.
        records[5]["mouse_x"] = 0.95  # large jump
        result = check2_mouse_position(records)
        assert result["passed"] is False
        assert result["mismatches"] >= 1

    def test_vacuous_when_no_mouse_field(self) -> None:
        records = [{"frame": 0, "keyCode": []}]
        result = check2_mouse_position(records)
        assert result["passed"] is True


# ---- Check 3: Quaternion ↔ Euler round-trip --------------------------------

class TestQuaternionEulerRoundTrip:
    def test_identity_passes(self) -> None:
        records = _build_clean_records(10)
        result = check3_quat_euler(records)
        assert result["passed"] is True
        assert result["mismatches"] == 0

    def test_yaw_45_round_trip(self) -> None:
        records = _build_clean_records(5)
        # Build a 45° yaw rotation and store both quat and euler.
        qx, qy, qz, qw = euler_zyx_to_quat(0.0, 0.0, 45.0)
        for r in records:
            r["camera_rotation_quaternion"] = [qx, qy, qz, qw]
            # The verifier compares (orig_field) vs (pitch_rt, yaw_rt, roll_rt).
            # Stored convention: [pitch, yaw, roll] per recorder PRD.
            r["camera_rotation_euler"] = [0.0, 45.0, 0.0]
        result = check3_quat_euler(records)
        assert result["passed"] is True

    def test_corrupt_pair_fails(self) -> None:
        records = _build_clean_records(5)
        for r in records:
            r["camera_rotation_quaternion"] = [0.0, 0.0, 0.0, 1.0]  # identity
            r["camera_rotation_euler"] = [0.0, 90.0, 0.0]  # NOT identity
        result = check3_quat_euler(records)
        assert result["passed"] is False
        assert result["mismatches"] == 5
        assert result["max_err_deg"] > 80

    def test_oula_field_alias_recognized(self) -> None:
        records = _build_clean_records(3)
        for r in records:
            r.pop("camera_rotation_euler", None)
            r["camera_rotation_oula"] = [0.0, 0.0, 0.0]
        result = check3_quat_euler(records)
        assert result["passed"] is True
        assert result["checked"] == 3


# ---- Check 4: Frame-time consistency ----------------------------------------

class TestFrameTimeConsistency:
    def test_clean_records_pass(self) -> None:
        records = _build_clean_records(60)
        result = check4_frame_time(records)
        assert result["passed"] is True

    def test_drift_caught(self) -> None:
        records = _build_clean_records(30)
        # Frame 10 says it's at second 5.0 but should be at 0.333s.
        records[10]["timestamp"] = 5.0
        result = check4_frame_time(records)
        assert result["passed"] is False
        assert result["mismatches"] >= 1

    def test_prd_time_string_parsed(self) -> None:
        records = []
        for i in range(20):
            ms = i * 33
            records.append({
                "frame": i,
                "time": f"2026-05-05 12:00:00.{ms:03d}",
                "fps": 30.0,
                "keyCode": [],
                "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
            })
        result = check4_frame_time(records)
        # Within the ±1 frame tolerance because 33ms ≈ 30fps.
        assert result["passed"] is True


# ---- CLI / loader tests ----------------------------------------------------

class TestLoaderAndCLI:
    def test_load_top_level_array(self, tmp_path: Path) -> None:
        records = _build_clean_records(5)
        clip = _write_clip(tmp_path, records)
        loaded = load_records(clip)
        assert len(loaded) == 5

    def test_load_records_wrapper(self, tmp_path: Path) -> None:
        records = _build_clean_records(5)
        clip = tmp_path / "clip"
        clip.mkdir()
        (clip / "action_camera.json").write_text(json.dumps({"records": records}))
        loaded = load_records(clip)
        assert len(loaded) == 5

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_records(tmp_path)

    def test_load_unknown_shape_raises(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip"
        clip.mkdir()
        (clip / "action_camera.json").write_text('"just a string"')
        with pytest.raises(ValueError):
            load_records(clip)

    def test_main_returns_zero_on_clean_clip(self, tmp_path: Path) -> None:
        clip = _write_clip(tmp_path, _build_clean_records(20))
        rc = main([str(clip)])
        assert rc == 0

    def test_main_returns_nonzero_on_corrupt(self, tmp_path: Path) -> None:
        records = _build_clean_records(10)
        records[5]["camera_rotation_euler"] = [0.0, 90.0, 0.0]  # corrupt vs identity quat
        clip = _write_clip(tmp_path, records)
        rc = main([str(clip)])
        assert rc >= 1

    def test_main_json_flag_emits_valid_json(self, tmp_path: Path, capsys) -> None:
        clip = _write_clip(tmp_path, _build_clean_records(5))
        main([str(clip), "--json"])
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["records"] == 5
        assert len(report["checks"]) == 4

    def test_main_returns_99_on_missing_clip(self, tmp_path: Path) -> None:
        rc = main([str(tmp_path / "nonexistent")])
        assert rc == 99


# ---- Run-all-checks aggregation -------------------------------------------

class TestRunAllChecks:
    def test_returns_four_checks(self) -> None:
        results = run_all_checks(_build_clean_records(10))
        assert len(results) == 4
        assert [c["check"] for c in results] == [1, 2, 3, 4]

    def test_clean_data_all_pass(self) -> None:
        results = run_all_checks(_build_clean_records(50))
        assert all(c["passed"] for c in results)
