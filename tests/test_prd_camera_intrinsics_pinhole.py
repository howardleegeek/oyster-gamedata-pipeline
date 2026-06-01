#!/usr/bin/env python3
"""Tests for bin/prd_test_camera_intrinsics_pinhole.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_camera_intrinsics_pinhole.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _write_json(data) -> Path:
    """Write data to a temp JSON file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# validate_pinhole_intrinsics unit tests (import-level)
# ---------------------------------------------------------------------------

class TestValidatePinholeIntrinsics:
    """Unit tests for the validate_pinhole_intrinsics function."""

    def _validate(self, camera, name="test"):
        """Import and call validate_pinhole_intrinsics."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("cam", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.validate_pinhole_intrinsics(camera, name)

    def test_fov_aspect_valid(self):
        """fov + aspect should pass."""
        errs = self._validate({"intrinsics": {"fov": 90, "aspect": 1.77}})
        assert errs == []

    def test_fx_fy_cx_cy_valid(self):
        """fx/fy/cx/cy should pass."""
        errs = self._validate({"intrinsics": {"fx": 500, "fy": 500, "cx": 320, "cy": 240}})
        assert errs == []

    def test_flat_fx_fy_cx_cy_valid(self):
        """Flat dict (no 'intrinsics' wrapper) with fx/fy/cx/cy should pass."""
        errs = self._validate({"fx": 500, "fy": 500, "cx": 320, "cy": 240})
        assert errs == []

    def test_missing_all_params(self):
        """No intrinsics at all should fail."""
        errs = self._validate({})
        assert len(errs) >= 1
        assert any("Missing required pinhole parameters" in e for e in errs)

    def test_fov_without_aspect(self):
        """fov without aspect should fail."""
        errs = self._validate({"intrinsics": {"fov": 90}})
        assert any("missing 'aspect'" in e for e in errs)

    def test_aspect_without_fov(self):
        """aspect without fov should fail."""
        errs = self._validate({"intrinsics": {"aspect": 1.77}})
        assert any("missing 'fov'" in e for e in errs)

    def test_negative_fov(self):
        """Negative fov should fail."""
        errs = self._validate({"intrinsics": {"fov": -90, "aspect": 1.77}})
        assert any("Invalid 'fov'" in e for e in errs)

    def test_negative_fx(self):
        """Negative fx should fail."""
        errs = self._validate({"intrinsics": {"fx": -500, "fy": 500, "cx": 320, "cy": 240}})
        assert any("Invalid 'fx'" in e for e in errs)

    def test_fisheye_model_rejected(self):
        """Fisheye projection model should fail."""
        errs = self._validate({
            "intrinsics": {
                "projection": {"model": "fisheye"},
                "fov": 90,
                "aspect": 1.77,
            }
        })
        assert any("Invalid projection model" in e for e in errs)

    def test_fisheye_key_rejected(self):
        """Fisheye distortion keys should fail."""
        errs = self._validate({
            "intrinsics": {
                "fov": 90,
                "aspect": 1.77,
                "fisheye": True,
            }
        })
        assert any("fisheye" in e.lower() for e in errs)

    def test_pinhole_model_accepted(self):
        """Explicit 'pinhole' model should pass."""
        errs = self._validate({
            "intrinsics": {
                "projection": {"model": "pinhole"},
                "fov": 90,
                "aspect": 1.77,
            }
        })
        assert errs == []

    def test_projection_fov_aspect(self):
        """fov/aspect inside projection should pass."""
        errs = self._validate({
            "intrinsics": {
                "projection": {"fov": 90, "aspect": 1.77},
            }
        })
        assert errs == []


# ---------------------------------------------------------------------------
# validate_cameras_file unit tests
# ---------------------------------------------------------------------------

class TestValidateCamerasFile:
    """Unit tests for the validate_cameras_file function."""

    def _validate_file(self, data):
        """Write data to temp file and validate."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("cam", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        path = _write_json(data)
        try:
            return mod.validate_cameras_file(path)
        finally:
            path.unlink()

    def test_single_camera_dict(self):
        """Single camera dict should validate."""
        data = {"camera1": {"intrinsics": {"fov": 90, "aspect": 1.77}}}
        ok, errs = self._validate_file(data)
        assert ok is True
        assert errs == []

    def test_cameras_key(self):
        """Dict with 'cameras' key should validate."""
        data = {"cameras": {"cam1": {"intrinsics": {"fov": 90, "aspect": 1.77}}}}
        ok, errs = self._validate_file(data)
        assert ok is True

    def test_action_camera_list_format(self):
        """List of records with camera_intrinsics should validate."""
        data = [
            {"camera_intrinsics": {"fx": 500, "fy": 500, "cx": 320, "cy": 240}},
            {"camera_intrinsics": {"fov": 90, "aspect": 1.77}},
        ]
        ok, errs = self._validate_file(data)
        assert ok is True
        assert errs == []

    def test_flat_list_format(self):
        """Flat list of camera dicts should validate."""
        data = [
            {"fx": 500, "fy": 500, "cx": 320, "cy": 240},
            {"fov": 90, "aspect": 1.77},
        ]
        ok, errs = self._validate_file(data)
        assert ok is True

    def test_invalid_json(self):
        """Invalid JSON should return error."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("cam", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        f.write("{invalid json")
        f.close()
        path = Path(f.name)
        try:
            ok, errs = mod.validate_cameras_file(path)
            assert ok is False
            assert len(errs) >= 1
        finally:
            path.unlink()

    def test_file_not_found(self):
        """Non-existent file should return error."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("cam", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        ok, errs = mod.validate_cameras_file(Path("/nonexistent/path.json"))
        assert ok is False
        assert len(errs) >= 1


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

class TestCameraIntrinsicsCLI:
    """Integration tests for the CLI entry point."""

    def test_help(self):
        """--help should show usage."""
        result = _run(["--help"])
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "Usage" in result.stdout

    def test_valid_fov_aspect_file(self):
        """Valid fov/aspect file should pass."""
        data = {"cam1": {"intrinsics": {"fov": 90, "aspect": 1.77}}}
        path = _write_json(data)
        try:
            result = _run([str(path)])
            assert result.returncode == 0
            assert "PASS" in result.stdout
        finally:
            path.unlink()

    def test_valid_fx_fy_file(self):
        """Valid fx/fy/cx/cy file should pass."""
        data = {"cam1": {"intrinsics": {"fx": 500, "fy": 500, "cx": 320, "cy": 240}}}
        path = _write_json(data)
        try:
            result = _run([str(path)])
            assert result.returncode == 0
        finally:
            path.unlink()

    def test_invalid_file_fails(self):
        """File with missing intrinsics should fail."""
        data = {"cam1": {}}
        path = _write_json(data)
        try:
            result = _run([str(path)])
            assert result.returncode == 1
            assert "FAIL" in result.stderr
        finally:
            path.unlink()

    def test_fisheye_file_fails(self):
        """File with fisheye model should fail."""
        data = {"cam1": {
            "intrinsics": {
                "projection": {"model": "fisheye"},
                "fov": 90, "aspect": 1.77,
            }
        }}
        path = _write_json(data)
        try:
            result = _run([str(path)])
            assert result.returncode == 1
        finally:
            path.unlink()

    def test_nonexistent_file(self):
        """Non-existent file should return exit code 2."""
        result = _run(["/nonexistent/file.json"])
        assert result.returncode == 2

    def test_verbose_flag(self):
        """--verbose should produce extra output."""
        data = {"cam1": {"intrinsics": {"fov": 90, "aspect": 1.77}}}
        path = _write_json(data)
        try:
            result = _run([str(path), "--verbose"])
            assert result.returncode == 0
            assert "pinhole" in result.stdout.lower()
        finally:
            path.unlink()

    def test_action_camera_list_format_cli(self):
        """action_camera.json list format should pass via CLI."""
        data = [
            {"camera_intrinsics": {"fx": 500, "fy": 500, "cx": 320, "cy": 240}},
        ]
        path = _write_json(data)
        try:
            result = _run([str(path)])
            assert result.returncode == 0
        finally:
            path.unlink()
