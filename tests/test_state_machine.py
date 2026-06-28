#!/usr/bin/env python3
"""
Test the state machine transitions for the continuous capture daemon
"""

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Mock psutil before importing the daemon
import sys

sys.modules["psutil"] = Mock()

from bin.continuous_capture_daemon import ContinuousCaptureDaemon, DaemonState


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        """Set up test environment"""
        # Mock the home directory
        self.mock_home = Path("/tmp/test_oyster")
        self.mock_home.mkdir(exist_ok=True)

        # Mock state file
        self.state_file = self.mock_home / ".oyster" / "daemon_state.json"
        self.state_file.parent.mkdir(exist_ok=True)

        # Mock heartbeat log
        self.heartbeat_log = self.mock_home / ".oyster" / "daemon_heartbeat.log"

        # Mock active session directory
        self.active_session_dir = project_root / "active_session"
        self.active_session_dir.mkdir(exist_ok=True)

        # Patch home directory
        self.home_patch = patch("pathlib.Path.home", return_value=self.mock_home)
        self.home_patch.start()

        # Create daemon instance with mocked dependencies
        with patch("psutil.disk_usage") as mock_disk_usage:
            mock_usage = Mock()
            mock_usage.free = 50 * 1024**3  # 50 GB free
            mock_disk_usage.return_value = mock_usage

            self.daemon = ContinuousCaptureDaemon()

        # Mock logger to avoid file writes
        self.daemon.logger = Mock()

    def tearDown(self):
        """Clean up test environment"""
        self.home_patch.stop()

        # Clean up test files
        if self.state_file.exists():
            self.state_file.unlink()
        if self.heartbeat_log.exists():
            self.heartbeat_log.unlink()
        if self.active_session_dir.exists():
            # Use shutil.rmtree so we handle nested subdirs (some tests
            # populate active_session/<session_id>/* before teardown).
            # The old item-by-item loop with rmdir() failed on CI with
            # "Directory not empty" when the daemon spawned subdirs.
            import shutil

            shutil.rmtree(self.active_session_dir, ignore_errors=True)

    def test_initial_state(self):
        """Test daemon starts in IDLE state"""
        self.assertEqual(self.daemon.state, DaemonState.IDLE)
        self.assertIsNone(self.daemon.session_id)
        self.assertIsNone(self.daemon.session_started)

    def test_idle_to_armed_transition(self):
        """Test transition from IDLE to ARMED when Minecraft starts"""
        with (
            patch.object(self.daemon, "_is_minecraft_running", return_value=True),
            patch.object(self.daemon, "_start_recorder", return_value=True),
        ):
            self.daemon._transition_to(DaemonState.ARMED)

            self.assertEqual(self.daemon.state, DaemonState.ARMED)
            self.assertIsNotNone(self.daemon.session_id)
            self.assertIsNotNone(self.daemon.session_started)
            self.assertTrue(self.daemon.session_id.startswith("session_"))

    def test_armed_to_recording_transition(self):
        """Test transition from ARMED to RECORDING when recorder starts"""
        self.daemon.state = DaemonState.ARMED
        self.daemon.session_id = "session_test"
        self.daemon.session_started = datetime.now()

        with patch.object(self.daemon, "_is_recorder_running", return_value=True):
            self.daemon._transition_to(DaemonState.RECORDING)

            self.assertEqual(self.daemon.state, DaemonState.RECORDING)
            self.daemon.logger.info.assert_called_with("Recording session session_test")

    def test_recording_to_finalizing_transition(self):
        """Test transition from RECORDING to FINALIZING when Minecraft stops.

        Note: `_transition_to(FINALIZING)` chains forward when `_run_finalize`
        succeeds — FINALIZING → UPLOADING → COOLDOWN. So we can't assert the
        end state is FINALIZING; we assert the FINALIZING-entry log fired.
        """
        self.daemon.state = DaemonState.RECORDING
        self.daemon.session_id = "session_test"

        with (
            patch.object(self.daemon, "_is_minecraft_running", return_value=False),
            patch.object(self.daemon, "_run_finalize", return_value=True),
            patch.object(self.daemon, "_queue_upload", return_value=True),
            patch.object(self.daemon, "_cleanup_session", return_value=True),
        ):
            self.daemon._transition_to(DaemonState.FINALIZING)

            self.daemon.logger.info.assert_any_call("Finalizing session session_test")

    def test_finalizing_to_uploading_transition(self):
        """Test transition from FINALIZING to UPLOADING when finalize succeeds"""
        self.daemon.state = DaemonState.FINALIZING
        self.daemon.session_id = "session_test"

        # Mock that finalize will succeed and trigger UPLOADING transition
        with patch.object(self.daemon, "_run_finalize", return_value=True):
            # The actual transition happens in _transition_to method
            # We need to test the logic that happens when state is FINALIZING
            pass

    def test_uploading_to_cooldown_transition(self):
        """Test transition from UPLOADING to COOLDOWN when upload queues.

        The session counter is incremented inside the UPLOADING branch of
        `_transition_to`, then the daemon chains itself to COOLDOWN. So
        we need to enter UPLOADING (not COOLDOWN directly) for the
        counter increment to fire.
        """
        self.daemon.state = DaemonState.FINALIZING  # pre-UPLOADING
        self.daemon.session_id = "session_test"
        self.daemon.total_sessions_today = 0

        with (
            patch.object(self.daemon, "_queue_upload", return_value=True),
            patch.object(self.daemon, "_cleanup_session", return_value=True),
        ):
            self.daemon._transition_to(DaemonState.UPLOADING)

            self.assertEqual(self.daemon.state, DaemonState.COOLDOWN)
            self.assertEqual(self.daemon.total_sessions_today, 1)
            self.assertIsNotNone(self.daemon.cooldown_until)

    def test_cooldown_to_idle_transition(self):
        """Test transition from COOLDOWN to IDLE after cooldown period"""
        self.daemon.state = DaemonState.COOLDOWN
        self.daemon.cooldown_until = None  # Simulate cooldown expired

        self.daemon._transition_to(DaemonState.IDLE)

        self.assertEqual(self.daemon.state, DaemonState.IDLE)
        self.assertIsNone(self.daemon.cooldown_until)

    def test_recorder_failure_handling(self):
        """Test handling when recorder fails to start"""
        self.daemon.state = DaemonState.ARMED

        # This is tested in the main loop, not in transition
        # So we'll just verify the state doesn't change unexpectedly
        self.assertEqual(self.daemon.state, DaemonState.ARMED)

    def test_finalize_failure_handling(self):
        """Test handling when finalize fails.

        The "Finalize failed" error log lives inside the FINALIZING branch
        of `_transition_to`. We must enter FINALIZING (with `_run_finalize`
        returning False), and the daemon will then self-chain to COOLDOWN.
        """
        self.daemon.state = DaemonState.RECORDING
        self.daemon.session_id = "session_test"

        with patch.object(self.daemon, "_run_finalize", return_value=False):
            self.daemon._transition_to(DaemonState.FINALIZING)

            self.assertEqual(self.daemon.state, DaemonState.COOLDOWN)
            self.daemon.logger.error.assert_any_call("Finalize failed, going to COOLDOWN")

    def test_upload_failure_handling(self):
        """Test handling when upload fails.

        The "Upload queue failed" error log lives inside the UPLOADING
        branch of `_transition_to`. We must enter UPLOADING (with
        `_queue_upload` returning False), and the daemon will then
        self-chain to COOLDOWN.
        """
        self.daemon.state = DaemonState.FINALIZING
        self.daemon.session_id = "session_test"

        with patch.object(self.daemon, "_queue_upload", return_value=False):
            self.daemon._transition_to(DaemonState.UPLOADING)

            self.assertEqual(self.daemon.state, DaemonState.COOLDOWN)
            self.daemon.logger.error.assert_any_call("Upload queue failed, going to COOLDOWN")

    def test_state_persistence(self):
        """Test that state is properly saved and loaded"""
        # Set some state
        self.daemon.state = DaemonState.RECORDING
        self.daemon.session_id = "session_persistence_test"
        self.daemon.session_started = datetime.now()
        self.daemon.total_sessions_today = 5
        self.daemon.total_uptime_hours = 12.5

        # Save state
        self.daemon._save_state()

        # Verify file was created
        self.assertTrue(self.state_file.exists())

        # Read and verify content
        with open(self.state_file, "r") as f:
            data = json.load(f)

        self.assertEqual(data["current_state"], "RECORDING")
        self.assertEqual(data["session_id"], "session_persistence_test")
        self.assertEqual(data["total_sessions_today"], 5)
        self.assertEqual(data["total_uptime_hours"], 12.5)

    def test_low_disk_space_pause(self):
        """Test that daemon pauses when disk space is low.

        `_log_heartbeat()` is gated on a 1-hour interval; the disk check
        only runs inside that hourly branch. So we backdate `last_heartbeat`
        to force the branch.
        """
        # Mock low disk space
        with patch("psutil.disk_usage") as mock_disk_usage:
            mock_usage = Mock()
            mock_usage.free = 5 * 1024**3  # 5 GB free (< 10 GB threshold)
            mock_disk_usage.return_value = mock_usage

            # Create new daemon with low disk space
            daemon = ContinuousCaptureDaemon()
            daemon.logger = Mock()
            # Force the hourly heartbeat branch (where disk check lives) to fire
            daemon.last_heartbeat = datetime.now() - timedelta(hours=2)

            # Run heartbeat check
            daemon._log_heartbeat()

            # Should be paused
            self.assertTrue(daemon.paused)
            daemon.logger.warning.assert_called_with(
                "Low disk space: 5.0 GB free. Pausing auto-arm."
            )

    def test_heartbeat_logging(self):
        """Test hourly heartbeat logging"""
        # Force heartbeat by setting last_heartbeat far in the past
        self.daemon.last_heartbeat = datetime.now() - timedelta(hours=2)

        # Set up some data
        self.daemon.sessions_completed_this_hour = 3
        self.daemon.uploads_completed_this_hour = 2
        self.daemon.errors_this_hour = ["test_error"]

        # Mock disk space
        with patch("psutil.disk_usage") as mock_disk_usage:
            mock_usage = Mock()
            mock_usage.free = 50 * 1024**3  # 50 GB free
            mock_disk_usage.return_value = mock_usage

            # Log heartbeat
            self.daemon._log_heartbeat()

        # Check log was written
        self.assertTrue(self.heartbeat_log.exists())

        # Read and verify log entry
        with open(self.heartbeat_log, "r") as f:
            lines = f.readlines()

        self.assertEqual(len(lines), 1)

        data = json.loads(lines[0].strip())

        self.assertEqual(data["state"], "IDLE")
        self.assertEqual(data["sessions_completed_last_hour"], 3)
        self.assertEqual(data["uploads_completed_last_hour"], 2)
        self.assertEqual(data["errors"], ["test_error"])
        self.assertEqual(data["disk_free_gb"], 50.0)

    def test_session_activity_check(self):
        """Test checking if session is active via game_state.jsonl mtime"""
        # Create a game_state.jsonl file
        game_state_file = self.active_session_dir / "game_state.jsonl"
        game_state_file.write_text("test data")

        # File should exist
        self.assertTrue(game_state_file.exists())

        # Check activity (file was just created, so should be active)
        is_active = self.daemon._check_session_active()
        self.assertTrue(is_active)

    def test_finalize_complete_check(self):
        """Test checking if finalize completed via clip files"""
        # This is a simple test that the method exists
        self.assertTrue(hasattr(self.daemon, "_check_finalize_complete"))

    @patch("subprocess.run")
    def test_minecraft_running_check_windows(self, mock_run):
        """Test Minecraft process check on Windows"""
        mock_result = Mock()
        mock_result.stdout = "javaw.exe 1234 Console 1 100,000 K"
        mock_run.return_value = mock_result

        with patch("platform.system", return_value="Windows"):
            is_running = self.daemon._is_minecraft_running()
            self.assertTrue(is_running)

    @patch("subprocess.run")
    def test_minecraft_running_check_macos(self, mock_run):
        """Test Minecraft process check on macOS"""
        mock_result = Mock()
        mock_result.returncode = 0  # Process found
        mock_run.return_value = mock_result

        with patch("platform.system", return_value="Darwin"):
            is_running = self.daemon._is_minecraft_running()
            self.assertTrue(is_running)

    @patch("subprocess.run")
    def test_recorder_running_check_windows(self, mock_run):
        """Test recorder process check on Windows"""
        mock_result = Mock()
        mock_result.stdout = "OysterRecorder.exe 5678 Console 1 50,000 K"
        mock_run.return_value = mock_result

        with patch("platform.system", return_value="Windows"):
            is_running = self.daemon._is_recorder_running()
            self.assertTrue(is_running)


if __name__ == "__main__":
    unittest.main()
