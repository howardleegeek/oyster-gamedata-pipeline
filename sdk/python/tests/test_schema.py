"""Unit tests for the schema dataclasses."""

from __future__ import annotations

import pytest

from oyster_gamedata_sdk import (
    ActionCameraFrame,
    CameraIntrinsics,
    MapBounds,
    SchemaValidationError,
    Systeminfo,
    Vector3,
    Vector4,
)


# -- Vector3 / Vector4 -------------------------------------------------------


class TestVector3:
    def test_from_list(self):
        v = Vector3.from_any([1.0, 2.0, 3.0])
        assert (v.x, v.y, v.z) == (1.0, 2.0, 3.0)
        assert v.as_tuple() == (1.0, 2.0, 3.0)

    def test_from_dict(self):
        v = Vector3.from_any({"x": 1, "y": 2, "z": 3})
        assert v.as_tuple() == (1.0, 2.0, 3.0)

    def test_from_tuple(self):
        v = Vector3.from_any((1.5, -2.0, 3.14))
        assert v.x == 1.5

    def test_rejects_wrong_length(self):
        with pytest.raises(SchemaValidationError):
            Vector3.from_any([1.0, 2.0])
        with pytest.raises(SchemaValidationError):
            Vector3.from_any([1.0, 2.0, 3.0, 4.0])

    def test_rejects_missing_key(self):
        with pytest.raises(SchemaValidationError):
            Vector3.from_any({"x": 1, "y": 2})

    def test_rejects_non_numeric(self):
        with pytest.raises(SchemaValidationError):
            Vector3.from_any(["a", "b", "c"])

    def test_frozen(self):
        v = Vector3(1.0, 2.0, 3.0)
        with pytest.raises(Exception):  # FrozenInstanceError
            v.x = 99  # type: ignore[misc]


class TestVector4:
    def test_from_list(self):
        v = Vector4.from_any([0.0, 1.0, 0.0, 1.0])
        assert v.w == 1.0

    def test_from_dict(self):
        v = Vector4.from_any({"x": 0, "y": 0, "z": 0, "w": 1})
        assert v.as_tuple() == (0.0, 0.0, 0.0, 1.0)

    def test_rejects_3_element_list(self):
        with pytest.raises(SchemaValidationError):
            Vector4.from_any([1, 2, 3])


# -- Systeminfo --------------------------------------------------------------


class TestSysteminfo:
    def _payload(self) -> dict:
        return {
            "gameProcessName": "minecraft.exe",
            "x": 0,
            "y": 0,
            "width": 1920,
            "height": 1080,
            "recordDpi": 1.0,
            "map_scale": 1.0,
            "map_bounds": {"min_x": -100, "min_z": -100, "max_x": 100, "max_z": 100},
        }

    def test_full_parse(self):
        si = Systeminfo.from_dict(self._payload())
        assert si.game_process_name == "minecraft.exe"
        assert si.width == 1920
        assert si.height == 1080
        assert isinstance(si.map_bounds, MapBounds)
        assert si.map_bounds.min_x == -100.0
        assert si.map_bounds.max_z == 100.0

    def test_missing_field(self):
        p = self._payload()
        del p["width"]
        with pytest.raises(SchemaValidationError, match="missing fields"):
            Systeminfo.from_dict(p)

    def test_bad_bounds(self):
        p = self._payload()
        p["map_bounds"] = {"min_x": -100}
        with pytest.raises(SchemaValidationError):
            Systeminfo.from_dict(p)

    def test_extra_fields_preserved(self):
        p = self._payload()
        p["custom_field"] = "value"
        si = Systeminfo.from_dict(p)
        assert si.extra == {"custom_field": "value"}


# -- CameraIntrinsics --------------------------------------------------------


class TestCameraIntrinsics:
    def test_pinhole(self):
        c = CameraIntrinsics.from_dict({"fx": 960.0, "fy": 960.0, "cx": 960, "cy": 540})
        assert c.is_pinhole

    def test_not_pinhole(self):
        c = CameraIntrinsics.from_dict({"fx": 960.0, "fy": 800.0, "cx": 960, "cy": 540})
        assert not c.is_pinhole

    def test_missing_field(self):
        with pytest.raises(SchemaValidationError):
            CameraIntrinsics.from_dict({"fx": 960.0, "fy": 960.0, "cx": 960})


# -- ActionCameraFrame -------------------------------------------------------


class TestActionCameraFrame:
    def _frame(self) -> dict:
        return {
            "frame": 0,
            "time": "2026-05-02 12:00:00.000",
            "fps": 30.0,
            "route_type": 1,
            "mouse_x": 0.5,
            "mouse_y": 0.5,
            "mouse_dx": 0.01,
            "mouse_dy": -0.02,
            "keyCode": [87],
            "camera_position": [0.0, 64.0, 0.0],
            "camera_rotation_oula": [0.0, -180.0, 0.0],
            "camera_rotation_quaternion": [0.0, -1.0, 0.0, 0.0],
            "camera_Follow Offset": [0.0, 1.6, 0.0],
            "camera_intrinsics": {"fx": 960.0, "fy": 960.0, "cx": 960.0, "cy": 540.0},
            "camera_speed": [1.5, 0.0, 0.0],
            "player_position": [0.0, 64.0, 0.0],
            "player_rotation_oula": [0.0, -180.0, 0.0],
            "player_rotation_quaternion": [0.0, -1.0, 0.0, 0.0],
            "player_speed": [1.5, 0.0, 0.0],
            "metric_scale": 1.0,
        }

    def test_parse_array_form(self):
        f = ActionCameraFrame.from_dict(self._frame())
        assert f.frame == 0
        assert f.fps == 30.0
        assert f.key_code == (87,)
        assert f.camera_position.as_tuple() == (0.0, 64.0, 0.0)
        assert f.camera_intrinsics.is_pinhole

    def test_parse_dict_form(self):
        """Per BUYER_SPEC_V1.md the vector form is {x,y,z}. Both must work."""
        p = self._frame()
        p["camera_position"] = {"x": 1.0, "y": 2.0, "z": 3.0}
        p["camera_rotation_quaternion"] = {"x": 0, "y": 0, "z": 0, "w": 1}
        f = ActionCameraFrame.from_dict(p)
        assert f.camera_position.as_tuple() == (1.0, 2.0, 3.0)
        assert f.camera_rotation_quaternion.w == 1.0

    def test_keycode_int_promotes_to_list(self):
        """Tolerate vendors who emit keyCode as a bare int."""
        p = self._frame()
        p["keyCode"] = 87
        f = ActionCameraFrame.from_dict(p)
        assert f.key_code == (87,)

    def test_keycode_invalid_type(self):
        p = self._frame()
        p["keyCode"] = "W"  # str — bad
        with pytest.raises(SchemaValidationError, match="keyCode"):
            ActionCameraFrame.from_dict(p)

    def test_keycode_empty_list(self):
        """An empty keyCode list means "no key pressed this frame" — valid."""
        p = self._frame()
        p["keyCode"] = []
        f = ActionCameraFrame.from_dict(p)
        assert f.key_code == ()

    def test_missing_camera_follow_offset(self):
        p = self._frame()
        del p["camera_Follow Offset"]
        with pytest.raises(SchemaValidationError, match="camera_Follow Offset"):
            ActionCameraFrame.from_dict(p)

    def test_missing_route_type(self):
        p = self._frame()
        del p["route_type"]
        with pytest.raises(SchemaValidationError, match="route_type"):
            ActionCameraFrame.from_dict(p)
