#!/usr/bin/env python3
"""
Tests for preflight_recorder.py
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add bin to path
sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

import preflight_recorder


class TestDisplayResolution(unittest.TestCase):
    """Test display resolution check."""

    @patch("subprocess.run")
    def test_resolution_1920x1080_passes(self, mock_run):
        """Mock display info → preflight produces correct OK for 1920x1080."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""Screen 0: minimum 8 x 8, current 1920 x 1080, maximum 32767 x 32767
HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right) 0mm x 0mm
   1920x1080     60.00*   60.00    """,
        )

        result = preflight_recorder.check_display_resolution()

        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "1920x1080")

    @patch("subprocess.run")
    def test_resolution_2560x1440_fails(self, mock_run):
        """Wrong resolution → preflight fails."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""Screen 0: minimum 8 x 8, current 2560 x 1440, maximum 32767 x 32767
HDMI-1 connected primary 2560x1440+0+0 (normal left inverted right) 0mm x 0mm
   2560x1440     60.00*   60.00    """,
        )

        result = preflight_recorder.check_display_resolution()

        self.assertFalse(result["ok"])
        self.assertEqual(result["value"], "2560x1440")

    @patch("subprocess.run")
    def test_resolution_1366x768_fails(self, mock_run):
        """Wrong resolution (laptop) → preflight fails."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""Screen 0: minimum 8 x 8, current 1366 x 768, maximum 32767 x 32767
eDP-1 connected primary 1366x768+0+0 (normal left inverted right) 0mm x 0mm
   1366x768     60.00*   60.00    """,
        )

        result = preflight_recorder.check_display_resolution()

        self.assertFalse(result["ok"])


class TestDPI(unittest.TestCase):
    """Test DPI check."""

    @patch("subprocess.run")
    def test_dpi_1_0_passes(self, mock_run):
        """DPI 1.0 → preflight passes."""
        # Use physical dimensions that give ~96 DPI (1.0 scale)
        # 1920 pixels / (1920/96 inches) = 1920 / 20 = 96 DPI
        # 20 inches = 508mm
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""Screen 0: minimum 8 x 8, current 1920 x 1080, maximum 32767 x 32767
HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right) 508mm x 285mm
   1920x1080     60.00*   60.00    """,
        )

        result = preflight_recorder.check_dpi()

        # DPI should be close to 1.0 (calculated from 508mm width: 1920/(508/25.4) ≈ 96 DPI, 96/96 = 1.0)
        self.assertTrue(result["ok"], f"Expected ok=True but got {result}")

    @patch("subprocess.run")
    def test_dpi_1_5_fails(self, mock_run):
        """Inject DPI=1.5 → preflight fails."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""Screen 0: minimum 8 x 8, current 1920 x 1080, maximum 32767 x 32767
HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right) 350mm x 200mm
   1920x1080     60.00*   60.00    """,
        )

        result = preflight_recorder.check_dpi()

        # DPI from 350mm: 1920/(350/25.4) ≈ 139 DPI, 139/96 ≈ 1.45
        self.assertFalse(result["ok"])

    @patch("subprocess.run")
    def test_dpi_2_0_fails(self, mock_run):
        """DPI 2.0 (HiDPI) → preflight fails."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""Screen 0: minimum 8 x 8, current 1920 x 1080, maximum 32767 x 32767
HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right) 260mm x 150mm
   1920x1080     60.00*   60.00    """,
        )

        result = preflight_recorder.check_dpi()

        # DPI from 260mm: 1920/(260/25.4) ≈ 188 DPI, 188/96 ≈ 1.96
        self.assertFalse(result["ok"])


class TestOverlappingWindows(unittest.TestCase):
    """Test overlapping windows check."""

    @patch("subprocess.run")
    def test_no_overlapping_passes(self, mock_run):
        """No overlapping windows → preflight passes."""
        # First call returns window IDs, subsequent calls return window names
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="1234\n5678\n9012"),  # search
            MagicMock(returncode=0, stdout="Minecraft - Java Edition"),  # getwindowname 1234
            MagicMock(returncode=0, stdout="Terminal"),  # getwindowname 5678
            MagicMock(returncode=0, stdout="File Manager"),  # getwindowname 9012
        ]

        result = preflight_recorder.check_overlapping_windows()

        self.assertTrue(result["ok"])

    @patch("subprocess.run")
    def test_discord_overlay_fails(self, mock_run):
        """Inject overlapping window (Discord) → preflight fails."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="1234\n5678\n9012"),  # search
            MagicMock(returncode=0, stdout="Minecraft - Java Edition"),  # getwindowname 1234
            MagicMock(returncode=0, stdout="Discord"),  # getwindowname 5678
            MagicMock(returncode=0, stdout="File Manager"),  # getwindowname 9012
        ]

        result = preflight_recorder.check_overlapping_windows()

        self.assertFalse(result["ok"])
        self.assertIn("Discord", str(result["value"]))

    @patch("subprocess.run")
    def test_obs_preview_fails(self, mock_run):
        """Inject overlapping window (OBS) → preflight fails."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="1234\n5678"),  # search
            MagicMock(returncode=0, stdout="Minecraft - Java Edition"),  # getwindowname 1234
            MagicMock(returncode=0, stdout="OBS Studio"),  # getwindowname 5678
        ]

        result = preflight_recorder.check_overlapping_windows()

        self.assertFalse(result["ok"])
        self.assertIn("OBS", str(result["value"]))

    @patch("subprocess.run")
    def test_geforce_experience_fails(self, mock_run):
        """Inject overlapping window (GeForce Experience) → preflight fails."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="1234\n5678"),  # search
            MagicMock(returncode=0, stdout="Minecraft - Java Edition"),  # getwindowname 1234
            MagicMock(returncode=0, stdout="GeForce Experience"),  # getwindowname 5678
        ]

        result = preflight_recorder.check_overlapping_windows()

        self.assertFalse(result["ok"])


class TestDiskSpace(unittest.TestCase):
    """Test disk space check."""

    @patch("subprocess.run")
    def test_disk_space_sufficient(self, mock_run):
        """Disk space >= 5GB → preflight passes."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""Filesystem  1G-blocks  Used Available Use% Mounted on
/dev/sda1       100G    50G    50G   50% /    """,
        )

        result = preflight_recorder.check_disk_space()

        self.assertTrue(result["ok"])
        self.assertIn("50GB", result["value"])

    @patch("subprocess.run")
    def test_disk_space_insufficient(self, mock_run):
        """Disk space < 5GB → preflight fails."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""Filesystem  1G-blocks  Used Available Use% Mounted on
/dev/sda1       100G    95G     3G   95% /    """,
        )

        result = preflight_recorder.check_disk_space()

        self.assertFalse(result["ok"])


class TestActiveSession(unittest.TestCase):
    """Test active session check."""

    def test_active_session_empty_passes(self):
        """active_session/ empty → preflight passes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            active_dir = Path(tmpdir) / "active_session"
            active_dir.mkdir()

            with patch("preflight_recorder.ACTIVE_SESSION_DIR", active_dir):
                result = preflight_recorder.check_active_session()

            self.assertTrue(result["ok"])

    def test_active_session_has_files_fails(self):
        """active_session/ has files → preflight fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            active_dir = Path(tmpdir) / "active_session"
            active_dir.mkdir()

            # Add some files
            (active_dir / "session.mp4").touch()
            (active_dir / "metadata.json").touch()

            with patch("preflight_recorder.ACTIVE_SESSION_DIR", active_dir):
                result = preflight_recorder.check_active_session()

            self.assertFalse(result["ok"])
            self.assertEqual(result["value"], "2 files")


class TestReportGeneration(unittest.TestCase):
    """Test report generation."""

    def test_report_structure(self):
        """Report has correct structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            report_path = output_dir / "preflight_report.json"

            # Mock all checks to pass
            with (
                patch("preflight_recorder.OUTPUT_DIR", output_dir),
                patch("preflight_recorder.REPORT_PATH", report_path),
                patch.object(
                    preflight_recorder,
                    "check_display_resolution",
                    return_value={
                        "name": "display_resolution",
                        "ok": True,
                        "value": "1920x1080",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_dpi",
                    return_value={"name": "dpi", "ok": True, "value": 1.0},
                ),
                patch.object(
                    preflight_recorder,
                    "check_minecraft_window",
                    return_value={
                        "name": "minecraft_window",
                        "ok": True,
                        "value": "ok",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_overlapping_windows",
                    return_value={
                        "name": "overlapping_windows",
                        "ok": True,
                        "value": "none",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_audio_device",
                    return_value={"name": "audio_device", "ok": True, "value": {}},
                ),
                patch.object(
                    preflight_recorder,
                    "check_fps",
                    return_value={"name": "fps_capability", "ok": True, "value": {}},
                ),
                patch.object(
                    preflight_recorder,
                    "check_disk_space",
                    return_value={
                        "name": "disk_space",
                        "ok": True,
                        "value": "50GB",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_oyster_recorder",
                    return_value={"name": "oyster_recorder", "ok": True, "value": {}},
                ),
                patch.object(
                    preflight_recorder,
                    "check_active_session",
                    return_value={
                        "name": "active_session",
                        "ok": True,
                        "value": "empty",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_network_tailscale",
                    return_value={"name": "network_tailscale", "ok": True, "value": {}},
                ),
            ):
                report = preflight_recorder.run_all_checks()

            self.assertTrue(report["all_pass"])
            self.assertIn("ran_at", report)
            self.assertEqual(len(report["checks"]), 10)

            # Verify all checks pass
            for check in report["checks"]:
                self.assertTrue(check["ok"])

    def test_report_fails_on_any_failure(self):
        """Report all_pass is False if any check fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            report_path = output_dir / "preflight_report.json"

            # Mock one check to fail
            with (
                patch("preflight_recorder.OUTPUT_DIR", output_dir),
                patch("preflight_recorder.REPORT_PATH", report_path),
                patch.object(
                    preflight_recorder,
                    "check_display_resolution",
                    return_value={
                        "name": "display_resolution",
                        "ok": False,
                        "value": "2560x1440",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_dpi",
                    return_value={"name": "dpi", "ok": True, "value": 1.0},
                ),
                patch.object(
                    preflight_recorder,
                    "check_minecraft_window",
                    return_value={
                        "name": "minecraft_window",
                        "ok": True,
                        "value": "ok",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_overlapping_windows",
                    return_value={
                        "name": "overlapping_windows",
                        "ok": True,
                        "value": "none",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_audio_device",
                    return_value={"name": "audio_device", "ok": True, "value": {}},
                ),
                patch.object(
                    preflight_recorder,
                    "check_fps",
                    return_value={"name": "fps_capability", "ok": True, "value": {}},
                ),
                patch.object(
                    preflight_recorder,
                    "check_disk_space",
                    return_value={
                        "name": "disk_space",
                        "ok": True,
                        "value": "50GB",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_oyster_recorder",
                    return_value={"name": "oyster_recorder", "ok": True, "value": {}},
                ),
                patch.object(
                    preflight_recorder,
                    "check_active_session",
                    return_value={
                        "name": "active_session",
                        "ok": True,
                        "value": "empty",
                    },
                ),
                patch.object(
                    preflight_recorder,
                    "check_network_tailscale",
                    return_value={"name": "network_tailscale", "ok": True, "value": {}},
                ),
            ):
                report = preflight_recorder.run_all_checks()

            self.assertFalse(report["all_pass"])


class TestOysterRecorderCheck(unittest.TestCase):
    """Test OysterRecorder check."""

    def test_oyster_recorder_not_found(self):
        """OysterRecorder not installed → preflight fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            with patch("preflight_recorder.OUTPUT_DIR", output_dir):
                result = preflight_recorder.check_oyster_recorder()

            self.assertFalse(result["ok"])

    def test_oyster_recorder_found(self):
        """OysterRecorder installed → preflight passes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            # Create the OysterRecorder.exe file
            recorder_path = output_dir / "OysterRecorder.exe"
            recorder_path.touch()

            with patch("preflight_recorder.OUTPUT_DIR", output_dir):
                result = preflight_recorder.check_oyster_recorder()

            self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
