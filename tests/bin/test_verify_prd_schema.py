#!/usr/bin/env python3
"""Tests for bin/verify_prd_schema.py — strict PRD-spec JSON schema validator.

Covers PRD `action_camera.json` per `docs/PRD.md` lines 117-131 and
`docs/PRD_AUDIT_2026_05_04.md` lines 70-81 field-type table.

Cases (5+ required by spec, this module ships 12):
  * good record (dict-form vectors)
  * good record (list-form vectors)
  * missing field
  * wrong type (frame as string)
  * out-of-range (mouse_x > 1)
  * fx != fy
  * route_type not in {1,2,3}
  * non-unit quaternion
  * keyCode wrong inner type (string instead of int)
  * camera_Follow Offset typo (missing space / wrong casing)
  * fps not 30.0
  * CLI exit code on bad clip
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from bin.verify_prd_schema import (  # noqa: E402
    main,
    validate_record,
    validate_records,
)


def _good_record(frame: int = 0) -> dict:
    """A canonical PRD-conformant record (dict-form Vector3/Vector4)."""
    return {
        "frame": frame,
        "time": "2026-05-02 15:30:45.000",
        "fps": 30.0,
        "route_type": 1,
        "mouse_x": 0.5,
        "mouse_y": 0.5,
        "mouse_dx": 0.01,
        "mouse_dy": -0.02,
        "keyCode": [87],
        "camera_position": {"x": 100.0, "y": 64.0, "z": 200.0},
        "camera_rotation_oula": {"pitch": 0.0, "yaw": 90.0, "roll": 0.0},
        "camera_rotation_quaternion": {"x": 0.0, "y": 0.7071068, "z": 0.0, "w": 0.7071068},
        "camera_Follow Offset": {"x": 0.0, "y": 1.6, "z": 0.0},
        "camera_intrinsics": {"fx": 960.0, "fy": 960.0, "cx": 960.0, "cy": 540.0},
        "camera_speed": {"x": 0.0, "y": 0.0, "z": 4.317},
        "player_position": {"x": 100.0, "y": 64.0, "z": 200.0},
        "player_rotation_oula": {"pitch": 0.0, "yaw": 90.0, "roll": 0.0},
        "player_rotation_quaternion": {"x": 0.0, "y": 0.7071068, "z": 0.0, "w": 0.7071068},
        "player_speed": {"x": 0.0, "y": 0.0, "z": 4.317},
        "metric_scale": 1.0,
    }


# ---- Case 1: well-formed record passes ------------------------------------


def test_good_record_dict_form_passes() -> None:
    """A canonical PRD record with dict-form vectors must pass."""
    violations = validate_record(_good_record(), index=0)
    assert violations == [], f"good record produced violations: {violations}"


# ---- Case 2: list-form vectors equally valid ------------------------------


def test_good_record_list_form_passes() -> None:
    """PRD allows Vector3 as `list[number]×3` and Vector4 as `list[number]×4`."""
    rec = _good_record()
    rec["camera_position"] = [100.0, 64.0, 200.0]
    rec["player_position"] = [100.0, 64.0, 200.0]
    rec["camera_speed"] = [0.0, 0.0, 4.317]
    rec["player_speed"] = [0.0, 0.0, 4.317]
    rec["camera_rotation_oula"] = [0.0, 90.0, 0.0]
    rec["player_rotation_oula"] = [0.0, 90.0, 0.0]
    rec["camera_rotation_quaternion"] = [0.0, 0.7071068, 0.0, 0.7071068]
    rec["player_rotation_quaternion"] = [0.0, 0.7071068, 0.0, 0.7071068]
    violations = validate_record(rec, index=0)
    assert violations == [], f"list-form record produced violations: {violations}"


# ---- Case 3: missing required field ---------------------------------------


def test_missing_field_reports() -> None:
    rec = _good_record()
    del rec["frame"]
    violations = validate_record(rec, index=0)
    assert any("frame" in v.lower() for v in violations), violations


def test_missing_camera_follow_offset_reports() -> None:
    """The literal field name 'camera_Follow Offset' is required (with space + capital F)."""
    rec = _good_record()
    del rec["camera_Follow Offset"]
    violations = validate_record(rec, index=0)
    assert any("Follow Offset" in v for v in violations), violations


# ---- Case 4: wrong type ---------------------------------------------------


def test_frame_wrong_type_reports() -> None:
    rec = _good_record()
    rec["frame"] = "0"  # str, not int
    violations = validate_record(rec, index=0)
    assert any("frame" in v for v in violations), violations


def test_keycode_inner_string_reports() -> None:
    """keyCode must be array of integers; an inner string is a violation."""
    rec = _good_record()
    rec["keyCode"] = ["87"]
    violations = validate_record(rec, index=0)
    assert any("keyCode" in v for v in violations), violations


# ---- Case 5: out-of-range -------------------------------------------------


def test_mouse_x_out_of_range_reports() -> None:
    rec = _good_record()
    rec["mouse_x"] = 1.5  # spec [0, 1]
    violations = validate_record(rec, index=0)
    assert any("mouse_x" in v for v in violations), violations


def test_mouse_dx_out_of_range_reports() -> None:
    rec = _good_record()
    rec["mouse_dx"] = 5.0  # spec [-1, 1]
    violations = validate_record(rec, index=0)
    assert any("mouse_dx" in v for v in violations), violations


def test_route_type_invalid_reports() -> None:
    rec = _good_record()
    rec["route_type"] = 7  # not in {1, 2, 3}
    violations = validate_record(rec, index=0)
    assert any("route_type" in v for v in violations), violations


# ---- Case 6: fx != fy -----------------------------------------------------


def test_fx_ne_fy_reports() -> None:
    rec = _good_record()
    rec["camera_intrinsics"] = {"fx": 960.0, "fy": 1080.0, "cx": 960.0, "cy": 540.0}
    violations = validate_record(rec, index=0)
    assert any("fx" in v and "fy" in v for v in violations), violations


# ---- Case 7: non-unit quaternion ------------------------------------------


def test_non_unit_quaternion_reports() -> None:
    rec = _good_record()
    rec["camera_rotation_quaternion"] = {"x": 1.0, "y": 1.0, "z": 1.0, "w": 1.0}  # ‖q‖ = 2
    violations = validate_record(rec, index=0)
    assert any("quaternion" in v for v in violations), violations


# ---- Case 8: fps not 30.0 -------------------------------------------------


def test_fps_wrong_value_reports() -> None:
    rec = _good_record()
    rec["fps"] = 60.0
    violations = validate_record(rec, index=0)
    assert any("fps" in v for v in violations), violations


# ---- Case 9: frame negative -----------------------------------------------


def test_frame_negative_reports() -> None:
    rec = _good_record()
    rec["frame"] = -1
    violations = validate_record(rec, index=0)
    assert any("frame" in v for v in violations), violations


# ---- Case 10: validate_records aggregates per-index -----------------------


def test_validate_records_aggregates_indices() -> None:
    good = _good_record(0)
    bad = _good_record(1)
    bad["fps"] = 60.0  # bad
    report = validate_records([good, bad])
    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["failed"] == 1
    assert any("[1]" in v or "frame=1" in v for v in report["violations"]), report


# ---- Case 11: CLI exit code on missing file -------------------------------


def test_cli_missing_file_exits_nonzero(tmp_path: Path) -> None:
    rc = main([str(tmp_path)])
    assert rc != 0  # action_camera.json not found


# ---- Case 12: CLI passes a good clip dir ----------------------------------


def test_cli_good_clip_passes(tmp_path: Path) -> None:
    clip = tmp_path / "clip"
    clip.mkdir()
    (clip / "action_camera.json").write_text(json.dumps([_good_record(0), _good_record(1)]))
    rc = main([str(clip)])
    assert rc == 0, "good clip should exit 0"


# ---- Case 13: CLI fails a bad clip dir ------------------------------------


def test_cli_bad_clip_fails(tmp_path: Path) -> None:
    clip = tmp_path / "clip"
    clip.mkdir()
    bad = _good_record(0)
    bad["fps"] = 60.0
    (clip / "action_camera.json").write_text(json.dumps([bad]))
    rc = main([str(clip)])
    assert rc != 0


# ---- Case 14: --json emits machine-readable report ------------------------


def test_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clip = tmp_path / "clip"
    clip.mkdir()
    (clip / "action_camera.json").write_text(json.dumps([_good_record(0)]))
    rc = main([str(clip), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["total"] == 1
    assert parsed["failed"] == 0
