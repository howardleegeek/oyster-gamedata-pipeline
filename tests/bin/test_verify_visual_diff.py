"""Tests for bin/verify_visual_diff.py — side-by-side action_camera differ."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin import verify_visual_diff as vvd

# ---- Fixtures --------------------------------------------------------------


def _write_clip(tmp_path: Path, name: str, records: list[dict]) -> Path:
    """Materialize a clip dir with action_camera.json on disk."""
    clip = tmp_path / name
    clip.mkdir(parents=True, exist_ok=True)
    (clip / "action_camera.json").write_text(json.dumps(records))
    return clip


@pytest.fixture
def base_records() -> list[dict]:
    return [
        {
            "frame_index": 0,
            "timestamp": 0.0,
            "mouse_dx": -3.0,
            "mouse_dy": -4.0,
            "camera_position": [-0.015, 0.0, 0.0],
            "camera_rotation_quaternion": [0.999888, 0.0, 0.0, -0.014999],
        },
        {
            "frame_index": 1,
            "timestamp": 0.033333,
            "mouse_dx": 0.0,
            "mouse_dy": 1.0,
            "camera_position": [-0.015, 0.000296, 0.05],
            "camera_rotation_quaternion": [0.999888, 0.0, 0.0, -0.014999],
        },
        {
            "frame_index": 2,
            "timestamp": 0.066667,
            "mouse_dx": 3.0,
            "mouse_dy": -3.0,
            "camera_position": [0.0, 0.000565, 0.1],
            "camera_rotation_quaternion": [1.0, 0.0, 0.0, 0.0],
        },
    ]


# ---- 1. Identical clips ----------------------------------------------------


class TestIdentical:
    def test_identical_returns_zero_exit(self, tmp_path, base_records, capsys):
        a = _write_clip(tmp_path, "a", base_records)
        b = _write_clip(tmp_path, "b", base_records)
        rc = vvd.main([str(a), str(b), "--no-color"])
        assert rc == 0

    def test_identical_structural_report(self, base_records):
        report = vvd.compute_structural_report(base_records, base_records)
        assert report.count_a == report.count_b
        assert report.field_diff.only_a == set()
        assert report.field_diff.only_b == set()
        assert report.field_diff.aliases == []
        assert report.pct_same_field_set == 100.0
        # No numeric drift on identical inputs
        assert all(d == 0.0 for d in report.mean_drift_per_field.values())

    def test_identical_per_frame_all_match(self, base_records):
        rows = vvd.diff_frame(base_records[0], base_records[0], aliases=[])
        assert all(r.match for r in rows)
        assert all(r.note == "" for r in rows)


# ---- 2. Missing keys (added / removed fields) ------------------------------


class TestMissingKey:
    def test_only_in_a_flagged(self, base_records):
        rec_a = dict(base_records[0])
        rec_b = dict(base_records[0])
        rec_b.pop("mouse_dy")
        rows = vvd.diff_frame(rec_a, rec_b, aliases=[])
        notes = {r.key: r.note for r in rows}
        assert notes["mouse_dy"] == "only in A"
        # Find the row, confirm it's marked as a diff
        row = next(r for r in rows if r.key == "mouse_dy")
        assert row.match is False
        assert row.val_b is vvd.SENTINEL_MISSING

    def test_only_in_b_flagged(self, base_records):
        rec_a = dict(base_records[0])
        rec_b = dict(base_records[0])
        rec_b["new_field"] = "extra"
        rows = vvd.diff_frame(rec_a, rec_b, aliases=[])
        row = next(r for r in rows if r.key == "new_field")
        assert row.match is False
        assert row.note == "only in B"
        assert row.val_a is vvd.SENTINEL_MISSING

    def test_structural_added_removed_keys(self, base_records):
        records_a = [{"a": 1, "shared": 1.0}]
        records_b = [{"b": 2, "shared": 1.0}]
        report = vvd.compute_structural_report(records_a, records_b)
        assert report.field_diff.only_a == {"a"}
        assert report.field_diff.only_b == {"b"}
        assert "shared" in report.field_diff.shared

    def test_main_exit_one_when_keys_differ(self, tmp_path, base_records):
        a = _write_clip(tmp_path, "a", [{"x": 1}])
        b = _write_clip(tmp_path, "b", [{"y": 2}])
        rc = vvd.main([str(a), str(b), "--no-color"])
        assert rc == 1


# ---- 3. Value drift on shared field ---------------------------------------


class TestValueDrift:
    def test_numeric_drift_detected(self, base_records):
        rec_a = {"timestamp": 0.0, "mouse_dx": 1.0}
        rec_b = {"timestamp": 0.0, "mouse_dx": 1.5}
        rows = vvd.diff_frame(rec_a, rec_b, aliases=[])
        ts_row = next(r for r in rows if r.key == "timestamp")
        dx_row = next(r for r in rows if r.key == "mouse_dx")
        assert ts_row.match is True
        assert dx_row.match is False

    def test_mean_drift_average(self):
        a = [{"x": 0.0}, {"x": 0.0}, {"x": 0.0}]
        b = [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}]
        report = vvd.compute_structural_report(a, b)
        # Mean |Δ| should be (1+2+3)/3 = 2.0
        assert report.mean_drift_per_field["x"] == pytest.approx(2.0)
        assert report.drift_sample_count_per_field["x"] == 3

    def test_drift_recurses_into_dict_vectors(self):
        a = [{"pos": {"x": 0.0, "y": 0.0, "z": 0.0}}]
        b = [{"pos": {"x": 0.5, "y": 0.0, "z": 0.0}}]
        report = vvd.compute_structural_report(a, b)
        assert report.mean_drift_per_field["pos.x"] == pytest.approx(0.5)
        assert report.mean_drift_per_field["pos.y"] == pytest.approx(0.0)

    def test_main_exit_one_on_drift(self, tmp_path):
        a = _write_clip(tmp_path, "a", [{"value": 1.0}])
        b = _write_clip(tmp_path, "b", [{"value": 2.0}])
        rc = vvd.main([str(a), str(b), "--no-color"])
        assert rc == 1


# ---- 4. List-vs-dict quaternion / vector normalization --------------------


class TestListVsDict:
    def test_list_form_quat_equals_dict_form_quat(self):
        list_form = [0.999888, 0.0, 0.0, -0.014999]
        dict_form = {"x": 0.999888, "y": 0.0, "z": 0.0, "w": -0.014999}
        rec_a = {"camera_rotation_quaternion": list_form}
        rec_b = {"camera_rotation_quaternion": dict_form}
        rows = vvd.diff_frame(rec_a, rec_b, aliases=[])
        # After normalization both sides should match.
        row = next(r for r in rows if r.key == "camera_rotation_quaternion")
        assert row.match is True

    def test_list_form_vec3_equals_dict_form_vec3(self):
        rec_a = {"camera_position": [1.0, 2.0, 3.0]}
        rec_b = {"camera_position": {"x": 1.0, "y": 2.0, "z": 3.0}}
        rows = vvd.diff_frame(rec_a, rec_b, aliases=[])
        row = next(r for r in rows if r.key == "camera_position")
        assert row.match is True

    def test_list_with_drift_still_diffs(self):
        rec_a = {"v": [1.0, 2.0, 3.0]}
        rec_b = {"v": {"x": 1.0, "y": 2.0, "z": 3.5}}
        rows = vvd.diff_frame(rec_a, rec_b, aliases=[])
        row = next(r for r in rows if r.key == "v")
        assert row.match is False

    def test_keycode_list_not_normalized(self):
        # keyCode is a list of strings — should NOT be coerced to {x,y,z}.
        rec_a = {"keyCode": ["w", "a"]}
        rec_b = {"keyCode": ["w", "a"]}
        rows = vvd.diff_frame(rec_a, rec_b, aliases=[])
        row = next(r for r in rows if r.key == "keyCode")
        assert row.match is True


# ---- 5. Field-name typo divergence (oula vs euler) ------------------------


class TestAliasDivergence:
    def test_oula_euler_aliased(self):
        records_a = [{"camera_rotation_oula": [10.0, 20.0, 30.0]}]
        records_b = [{"camera_rotation_euler": [10.0, 20.0, 30.0]}]
        report = vvd.compute_structural_report(records_a, records_b)
        # The alias should be detected and removed from only_a / only_b.
        assert ("camera_rotation_oula", "camera_rotation_euler") in report.field_diff.aliases
        assert "camera_rotation_oula" not in report.field_diff.only_a
        assert "camera_rotation_euler" not in report.field_diff.only_b

    def test_alias_row_paired_in_frame_diff(self):
        rec_a = {"camera_rotation_oula": [10.0, 20.0, 30.0]}
        rec_b = {"camera_rotation_euler": [10.0, 20.0, 30.0]}
        rows = vvd.diff_frame(
            rec_a, rec_b, aliases=[("camera_rotation_oula", "camera_rotation_euler")]
        )
        assert len(rows) == 1
        assert "alias" in rows[0].note
        # Even with values equal, they should match because we coerce list→dict
        assert rows[0].match is True


# ---- 6. CLI / output formats ----------------------------------------------


class TestCli:
    def test_default_frames_are_first_mid_last(self, base_records):
        frames = vvd.pick_default_frames(len(base_records), len(base_records))
        assert frames == [0, 1, 2]

    def test_explicit_frames_override(self, base_records):
        frames = vvd.parse_frames_arg("0,2", len(base_records), len(base_records))
        assert frames == [0, 2]

    def test_html_output_is_html(self, tmp_path, base_records):
        a = _write_clip(tmp_path, "a", base_records)
        b = _write_clip(tmp_path, "b", base_records)
        out_file = tmp_path / "report.html"
        rc = vvd.main([str(a), str(b), "--html", "--output", str(out_file)])
        assert rc == 0
        text = out_file.read_text()
        assert "<!DOCTYPE html>" in text
        assert "verify_visual_diff" in text

    def test_json_output_is_parseable(self, tmp_path, base_records, capsys):
        a = _write_clip(tmp_path, "a", base_records)
        b = _write_clip(tmp_path, "b", base_records)
        rc = vvd.main([str(a), str(b), "--json"])
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["structural"]["count_a"] == 3
        assert payload["structural"]["count_b"] == 3
        assert "frames" in payload
        assert rc == 0

    def test_missing_file_exit_99(self, tmp_path, capsys):
        a = tmp_path / "nope_a"
        b = tmp_path / "nope_b"
        a.mkdir()
        b.mkdir()
        rc = vvd.main([str(a), str(b)])
        assert rc == 99
