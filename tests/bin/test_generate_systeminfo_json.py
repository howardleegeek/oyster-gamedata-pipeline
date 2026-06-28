#!/usr/bin/env python3
"""
Tests for generate_systeminfo_json.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add the bin directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

from generate_systeminfo_json import (
    build_systeminfo,
    detect_screen_dpi,
    detect_window_geometry,
    main,
    write_systeminfo_json,
)


class TestDetectScreenDPI(unittest.TestCase):
    """Test detect_screen_dpi function."""

    @patch("subprocess.run")
    def test_detect_screen_dpi_macos_success(self, mock_run):
        """Test macOS DPI detection success."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "2.0\n"
        mock_run.return_value = mock_result

        with patch("sys.platform", "darwin"):
            dpi = detect_screen_dpi()
            self.assertEqual(dpi, 2.0)

    @patch("subprocess.run")
    def test_detect_screen_dpi_macos_failure(self, mock_run):
        """Test macOS DPI detection failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        with patch("sys.platform", "darwin"):
            dpi = detect_screen_dpi()
            self.assertEqual(dpi, 1.0)

    @patch("subprocess.run")
    def test_detect_screen_dpi_linux_success(self, mock_run):
        """Test Linux DPI detection success."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 8192 x 8192\nscale factor: 1.5\n"
        mock_run.return_value = mock_result

        with patch("sys.platform", "linux"):
            dpi = detect_screen_dpi()
            self.assertEqual(dpi, 1.5)  # Should parse "scale factor: 1.5"

    @patch("subprocess.run")
    def test_detect_screen_dpi_linux_failure(self, mock_run):
        """Test Linux DPI detection failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        with patch("sys.platform", "linux"):
            dpi = detect_screen_dpi()
            self.assertEqual(dpi, 1.0)

    def test_detect_screen_dpi_unknown_platform(self):
        """Test DPI detection on unknown platform."""
        with patch("sys.platform", "win32"):
            dpi = detect_screen_dpi()
            self.assertEqual(dpi, 1.0)

    @patch("subprocess.run")
    def test_detect_screen_dpi_timeout(self, mock_run):
        """Test DPI detection with timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 2)

        dpi = detect_screen_dpi()
        self.assertEqual(dpi, 1.0)


class TestDetectWindowGeometry(unittest.TestCase):
    """Test detect_window_geometry function."""

    @patch("subprocess.run")
    def test_detect_window_geometry_macos_success(self, mock_run):
        """Test macOS window geometry detection success."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "100, 200, 1300, 900\n"
        mock_run.return_value = mock_result

        with patch("sys.platform", "darwin"):
            geometry = detect_window_geometry("Minecraft")
            self.assertEqual(
                geometry,
                {
                    "x": 100,
                    "y": 200,
                    "width": 1200,  # 1300 - 100
                    "height": 700,  # 900 - 200
                },
            )

    @patch("subprocess.run")
    def test_detect_window_geometry_macos_failure(self, mock_run):
        """Test macOS window geometry detection failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        with patch("sys.platform", "darwin"):
            geometry = detect_window_geometry("Minecraft")
            self.assertEqual(
                geometry,
                {
                    "x": 0,
                    "y": 0,
                    "width": 1920,
                    "height": 1080,
                },
            )

    @patch("subprocess.run")
    def test_detect_window_geometry_linux_success(self, mock_run):
        """Test Linux window geometry detection success."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "X=100\nY=200\nWIDTH=1200\nHEIGHT=700\n"
        mock_run.return_value = mock_result

        with patch("sys.platform", "linux"):
            geometry = detect_window_geometry("Minecraft")
            self.assertEqual(
                geometry,
                {
                    "x": 100,
                    "y": 200,
                    "width": 1200,
                    "height": 700,
                },
            )

    @patch("subprocess.run")
    def test_detect_window_geometry_linux_failure(self, mock_run):
        """Test Linux window geometry detection failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        with patch("sys.platform", "linux"):
            geometry = detect_window_geometry("Minecraft")
            self.assertEqual(
                geometry,
                {
                    "x": 0,
                    "y": 0,
                    "width": 1920,
                    "height": 1080,
                },
            )

    def test_detect_window_geometry_unknown_platform(self):
        """Test window geometry detection on unknown platform."""
        with patch("sys.platform", "win32"):
            geometry = detect_window_geometry("Minecraft")
            self.assertEqual(
                geometry,
                {
                    "x": 0,
                    "y": 0,
                    "width": 1920,
                    "height": 1080,
                },
            )

    @patch("subprocess.run")
    def test_detect_window_geometry_timeout(self, mock_run):
        """Test window geometry detection with timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)

        geometry = detect_window_geometry("Minecraft")
        self.assertEqual(
            geometry,
            {
                "x": 0,
                "y": 0,
                "width": 1920,
                "height": 1080,
            },
        )


class TestBuildSysteminfo(unittest.TestCase):
    """Test build_systeminfo function."""

    def test_build_systeminfo_default_values(self):
        """Test building systeminfo with PRD-canonical 5-field defaults.

        PRD page 3-4 schema = 5 fields. map_scale/map_bounds are legacy
        opt-in extras — see test_build_systeminfo_with_legacy_extras.
        """
        result = build_systeminfo()

        self.assertEqual(result["gameProcessName"], "minecraft.exe")
        self.assertEqual(result["x"], 0)
        self.assertEqual(result["y"], 0)
        self.assertEqual(result["width"], 1920)
        self.assertEqual(result["height"], 1080)
        self.assertEqual(result["recordDpi"], 1.0)
        # PRD compliance: legacy fields must NOT appear by default.
        self.assertNotIn("map_scale", result)
        self.assertNotIn("map_bounds", result)

    def test_build_systeminfo_with_legacy_extras(self):
        """Legacy extras path — only emitted when explicitly opted in."""
        result = build_systeminfo(include_legacy_extras=True)
        self.assertEqual(result["map_scale"], 1.0)
        self.assertEqual(
            result["map_bounds"],
            {
                "min_x": -10000,
                "min_z": -10000,
                "max_x": 10000,
                "max_z": 10000,
            },
        )

    def test_build_systeminfo_with_overrides(self):
        """Test building systeminfo with custom values."""
        custom_map_bounds = {
            "min_x": -5000,
            "min_z": -5000,
            "max_x": 5000,
            "max_z": 5000,
        }

        result = build_systeminfo(
            game_process_name="custom_game.exe",
            x=100,
            y=200,
            width=2560,
            height=1440,
            record_dpi=2.0,
            map_scale=0.5,
            map_bounds=custom_map_bounds,
            include_legacy_extras=True,
        )

        self.assertEqual(result["gameProcessName"], "custom_game.exe")
        self.assertEqual(result["x"], 100)
        self.assertEqual(result["y"], 200)
        self.assertEqual(result["width"], 2560)
        self.assertEqual(result["height"], 1440)
        self.assertEqual(result["recordDpi"], 2.0)
        self.assertEqual(result["map_scale"], 0.5)
        self.assertEqual(result["map_bounds"], custom_map_bounds)

    def test_build_systeminfo_map_bounds_default_minecraft(self):
        """map_bounds default Minecraft values when legacy extras enabled."""
        result = build_systeminfo(map_bounds=None, include_legacy_extras=True)

        self.assertEqual(
            result["map_bounds"],
            {
                "min_x": -10000,
                "min_z": -10000,
                "max_x": 10000,
                "max_z": 10000,
            },
        )

    def test_build_systeminfo_dpi_validation(self):
        """Test that record_dpi must be greater than 0."""
        with self.assertRaises(ValueError) as context:
            build_systeminfo(record_dpi=0)

        self.assertIn("record_dpi must be greater than 0", str(context.exception))

        with self.assertRaises(ValueError) as context:
            build_systeminfo(record_dpi=-1.0)

        self.assertIn("record_dpi must be greater than 0", str(context.exception))

    def test_build_systeminfo_positive_dpi(self):
        """Test that positive DPI values are accepted."""
        result = build_systeminfo(record_dpi=0.5)
        self.assertEqual(result["recordDpi"], 0.5)

        result = build_systeminfo(record_dpi=1.5)
        self.assertEqual(result["recordDpi"], 1.5)

        result = build_systeminfo(record_dpi=2.0)
        self.assertEqual(result["recordDpi"], 2.0)


class TestWriteSysteminfoJson(unittest.TestCase):
    """Test write_systeminfo_json function."""

    def test_write_then_read_roundtrip(self):
        """Test writing and reading systeminfo.json."""
        test_data = {
            "gameProcessName": "test_game.exe",
            "x": 50,
            "y": 60,
            "width": 1280,
            "height": 720,
            "recordDpi": 1.5,
            "map_scale": 0.8,
            "map_bounds": {
                "min_x": -8000,
                "min_z": -8000,
                "max_x": 8000,
                "max_z": 8000,
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            # Write the data
            write_systeminfo_json(test_data, temp_path)

            # Read it back
            with open(temp_path, encoding="utf-8") as f:
                loaded_data = json.load(f)

            # Compare
            self.assertEqual(loaded_data, test_data)
        finally:
            os.unlink(temp_path)

    def test_write_systeminfo_json_creates_valid_json(self):
        """Test that written JSON is valid (legacy-extras path)."""
        test_data = build_systeminfo(include_legacy_extras=True)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            # Write the data
            write_systeminfo_json(test_data, temp_path)

            # Verify it's valid JSON
            with open(temp_path, encoding="utf-8") as f:
                content = f.read()
                parsed = json.loads(content)

            # Check structure (legacy-extras schema)
            self.assertIn("gameProcessName", parsed)
            self.assertIn("x", parsed)
            self.assertIn("y", parsed)
            self.assertIn("width", parsed)
            self.assertIn("height", parsed)
            self.assertIn("recordDpi", parsed)
            self.assertIn("map_scale", parsed)
            self.assertIn("map_bounds", parsed)
        finally:
            os.unlink(temp_path)


class TestMainFunction(unittest.TestCase):
    """Test main function."""

    def test_main_writes_file(self):
        """Test that main function writes a file with correct content."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            # Run main with arguments (legacy extras path)
            test_args = [
                "--output",
                temp_path,
                "--game-process-name",
                "my_game.exe",
                "--x",
                "100",
                "--y",
                "200",
                "--width",
                "2560",
                "--height",
                "1440",
                "--record-dpi",
                "2.0",
                "--map-scale",
                "0.75",
                "--map-bounds-min-x",
                "-5000",
                "--map-bounds-min-z",
                "-5000",
                "--map-bounds-max-x",
                "5000",
                "--map-bounds-max-z",
                "5000",
                "--include-legacy-extras",
            ]

            with patch("sys.argv", ["generate_systeminfo_json.py"] + test_args):
                return_code = main()

            self.assertEqual(return_code, 0)

            # Read and verify the file
            with open(temp_path, encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["gameProcessName"], "my_game.exe")
            self.assertEqual(data["x"], 100)
            self.assertEqual(data["y"], 200)
            self.assertEqual(data["width"], 2560)
            self.assertEqual(data["height"], 1440)
            self.assertEqual(data["recordDpi"], 2.0)
            self.assertEqual(data["map_scale"], 0.75)
            self.assertEqual(
                data["map_bounds"],
                {
                    "min_x": -5000,
                    "min_z": -5000,
                    "max_x": 5000,
                    "max_z": 5000,
                },
            )
        finally:
            os.unlink(temp_path)

    def test_main_with_auto_detect_flags(self):
        """Test main function with auto-detect flags."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            # Mock the detection functions
            with patch("generate_systeminfo_json.detect_screen_dpi") as mock_dpi, \
                 patch("generate_systeminfo_json.detect_window_geometry") as mock_geo:
                mock_dpi.return_value = 1.5
                mock_geo.return_value = {
                    "x": 150,
                    "y": 250,
                    "width": 1920,
                    "height": 1080,
                }

                # Run main with auto-detect flags
                test_args = [
                    "--output",
                    temp_path,
                    "--auto-detect-dpi",
                    "--auto-detect-window",
                ]

                with patch("sys.argv", ["generate_systeminfo_json.py"] + test_args):
                    return_code = main()

                    self.assertEqual(return_code, 0)

                    # Verify mocks were called
                    mock_dpi.assert_called_once()
                    mock_geo.assert_called_once()  # Called without arguments, uses default

                    # Read and verify the file
                    with open(temp_path, encoding="utf-8") as f:
                        data = json.load(f)

                    self.assertEqual(data["recordDpi"], 1.5)
                    self.assertEqual(data["x"], 150)
                    self.assertEqual(data["y"], 250)
                    self.assertEqual(data["width"], 1920)
                    self.assertEqual(data["height"], 1080)
        finally:
            os.unlink(temp_path)

    def test_main_with_invalid_dpi(self):
        """Test main function with invalid DPI value."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            # Run main with invalid DPI
            test_args = [
                "--output",
                temp_path,
                "--record-dpi",
                "0.0",  # Invalid: must be > 0
            ]

            with patch("sys.argv", ["generate_systeminfo_json.py"] + test_args):
                return_code = main()

            self.assertEqual(return_code, 1)  # Should fail
        finally:
            os.unlink(temp_path)

    def test_main_missing_output_argument(self):
        """Test main function without required output argument."""
        # Capture stderr
        import io
        from contextlib import redirect_stderr

        stderr_capture = io.StringIO()

        # This should raise SystemExit due to argparse
        with redirect_stderr(stderr_capture), \
             patch("sys.argv", ["generate_systeminfo_json.py"]), \
             self.assertRaises(SystemExit):
            main()

        # Check that error message mentions --output
        stderr_output = stderr_capture.getvalue()
        self.assertIn("--output", stderr_output)


if __name__ == "__main__":
    unittest.main()
