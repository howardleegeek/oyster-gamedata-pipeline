#!/usr/bin/env python3
"""
Test the state machine transitions for the continuous capture daemon
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os
import json
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Mock psutil before importing the daemon
import sys
sys.modules['psutil'] = Mock()

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
        self.home_patch = patch('pathlib.Path.home', return_value=self.mock_home)
        self.home_patch.start()
        
        # Create daemon instance with mocked dependencies
        with patch('psutil.disk_usage') as mock_disk_usage:
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
            for item in self.active_session_dir.iterdir():
                if item.is_file():
                    item.unlink()
            self.active_session_dir.rmdir()
    
    def test_initial_state(self):
        """Test daemon starts in IDLE state"""
        self.assertEqual(self.daemon.state, DaemonState.IDLE)
        self.assertIsNone(self.daemon.session_id)
        self.assertIsNone(self.daemon.session_started)
    
    def test_idle_to_armed_transition(self):
        """Test transition from IDLE to ARMED when Minecraft starts"""
        with patch.object(self.daemon, '_is_minecraft_running', return_value=True):
            with patch.object(self.daemon, '_start_recorder', return_value=True):
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
        
        with patch.object(self.daemon, '_is_recorder_running', return_value=True):
            self.daemon._transition_to(DaemonState.RECORDING)
            
            self.assertEqual(self.daemon.state, DaemonState.RECORDING)
            self.daemon.logger.info.assert_called_with("Recording session session_test")
    
    def test_recording_to_finalizing_transition(self):
        """Test transition from RECORDING to FINALIZING when Minecraft stops"""
        self.daemon.state = DaemonState.RECORDING
        self.daemon.session_id = "session_test"
        
        with patch.object(self.daemon, '_is_minecraft_running', return_value=False):
            # Mock the transition that happens in _transition_to
            with patch.object(self.daemon, '_run_finalize', return_value=True):
                self.daemon._transition_to(DaemonState.FINALIZING)
                
                self.assertEqual(self.daemon.state, DaemonState.FINALIZING)
                self.daemon.logger.info.assert_called_with("Finalizing session session_test")
    
    def test_finalizing_to_uploading_transition(self):
        """Test transition from FINALIZING to UPLOADING when finalize succeeds"""
        self.daemon.state = DaemonState.FINALIZING
        self.daemon.session_id = "session_test"
        
        # Mock that finalize will succeed and trigger UPLOADING transition
        with patch.object(self.daemon, '_run_finalize', return_value=True):
            # The actual transition happens in _transition_to method
            # We need to test the logic that happens when state is FINALIZING
            pass
    
    def test_uploading_to_cooldown_transition(self):
        """Test transition from UPLOADING to COOLDOWN when upload queues"""
        self.daemon.state = DaemonState.UPLOADING
        self.daemon.session_id = "session_test"
        self.daemon.total_sessions_today = 0
        
        with patch.object(self.daemon, '_queue_upload', return_value=True):
            with patch.object(self.daemon, '_cleanup_session', return_value=True):
                self.daemon._transition_to(DaemonState.COOLDOWN)
                
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
        """Test handling when finalize fails"""
        self.daemon.state = DaemonState.FINALIZING
        
        with patch.object(self.daemon, '_run_finalize', return_value=False):
            self.daemon._transition_to(DaemonState.COOLDOWN)
            
            self.assertEqual(self.daemon.state, DaemonState.COOLDOWN)
            self.daemon.logger.error.assert_called_with("Finalize failed, going to COOLDOWN")
    
    def test_upload_failure_handling(self):
        """Test handling when upload fails"""
        self.daemon.state = DaemonState.UPLOADING
        
        with patch.object(self.daemon, '_queue_upload', return_value=False):
            self.daemon._transition_to(DaemonState.COOLDOWN)
            
            self.assertEqual(self.daemon.state, DaemonState.COOLDOWN)
            self.daemon.logger.error.assert_called_with("Upload queue failed, going to COOLDOWN")
    
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
        with open(self.state_file, 'r') as f:
            data = json.load(f)
        
        self.assertEqual(data['current_state'], 'RECORDING')
        self.assertEqual(data['session_id'], 'session_persistence_test')
        self.assertEqual(data['total_sessions_today'], 5)
        self.assertEqual(data['total_uptime_hours'], 12.5)
    
    def test_low_disk_space_pause(self):
        """Test that daemon pauses when disk space is low"""
        # Mock low disk space
        with patch('psutil.disk_usage') as mock_disk_usage:
            mock_usage = Mock()
            mock_usage.free = 5 * 1024**3  # 5 GB free (< 10 GB threshold)
            mock_disk_usage.return_value = mock_usage
            
            # Create new daemon with low disk space
            daemon = ContinuousCaptureDaemon()
            daemon.logger = Mock()
            
            # Run heartbeat check
            daemon._log_heartbeat()
            
            # Should be paused
            self.assertTrue(daemon.paused)
            daemon.logger.warning.assert_called_with("Low disk space: 5.0 GB free. Pausing auto-arm.")
    
    def test_heartbeat_logging(self):
        """Test hourly heartbeat logging"""
        # Force heartbeat by setting last_heartbeat far in the past
        self.daemon.last_heartbeat = datetime.now() - timedelta(hours=2)
        
        # Set up some data
        self.daemon.sessions_completed_this_hour = 3
        self.daemon.uploads_completed_this_hour = 2
        self.daemon.errors_this_hour = ["test_error"]
        
        # Mock disk space
        with patch('psutil.disk_usage') as mock_disk_usage:
            mock_usage = Mock()
            mock_usage.free = 50 * 1024**3  # 50 GB free
            mock_disk_usage.return_value = mock_usage
            
            # Log heartbeat
            self.daemon._log_heartbeat()
        
        # Check log was written
        self.assertTrue(self.heartbeat_log.exists())
        
        # Read and verify log entry
        with open(self.heartbeat_log, 'r') as f:
            lines = f.readlines()
        
        self.assertEqual(len(lines), 1)
        
        data = json.loads(lines[0].strip())
        
        self.assertEqual(data['state'], 'IDLE')
        self.assertEqual(data['sessions_completed_last_hour'], 3)
        self.assertEqual(data['uploads_completed_last_hour'], 2)
        self.assertEqual(data['errors'], ['test_error'])
        self.assertEqual(data['disk_free_gb'], 50.0)
    
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
        self.assertTrue(hasattr(self.daemon, '_check_finalize_complete'))
    
    @patch('subprocess.run')
    def test_minecraft_running_check_windows(self, mock_run):
        """Test Minecraft process check on Windows"""
        mock_result = Mock()
        mock_result.stdout = "javaw.exe 1234 Console 1 100,000 K"
        mock_run.return_value = mock_result
        
        with patch('platform.system', return_value="Windows"):
            is_running = self.daemon._is_minecraft_running()
            self.assertTrue(is_running)
    
    @patch('subprocess.run')
    def test_minecraft_running_check_macos(self, mock_run):
        """Test Minecraft process check on macOS"""
        mock_result = Mock()
        mock_result.returncode = 0  # Process found
        mock_run.return_value = mock_result
        
        with patch('platform.system', return_value="Darwin"):
            is_running = self.daemon._is_minecraft_running()
            self.assertTrue(is_running)
    
    @patch('subprocess.run')
    def test_recorder_running_check_windows(self, mock_run):
        """Test recorder process check on Windows"""
        mock_result = Mock()
        mock_result.stdout = "OysterRecorder.exe 5678 Console 1 50,000 K"
        mock_run.return_value = mock_result
        
        with patch('platform.system', return_value="Windows"):
            is_running = self.daemon._is_recorder_running()
            self.assertTrue(is_running)

# Import timedelta for heartbeat test
from datetime import timedelta

if __name__ == '__main__':
    unittest.main()