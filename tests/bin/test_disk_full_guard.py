#!/usr/bin/env python3
"""
Tests for bin/disk_full_guard.py — disk space monitor for capture

Purpose:
QA audit BLOCKER: disk-full mid-capture is uncaught, produces silently
truncated tarballs. This independent guard runs alongside capture, kills
the parent if free space < threshold.

Test coverage:
- get_free_gb (normal path, path not found, permission error)
- watch_loop (free above threshold stays running, drops below triggers SIGTERM,
  process not found, permission denied, custom check_interval)
- main() CLI (--path/--min-gb/--parent-pid/--check-interval, exit codes)
"""

import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))
from bin.disk_full_guard import get_free_gb, main, watch_loop


class TestGetFreeGb:
    """Tests for get_free_gb function."""

    def test_free_gb_returns_expected_value(self):
        """Test that get_free_gb returns correct GB value from disk_usage."""
        with patch("bin.disk_full_guard.shutil.disk_usage") as mock_usage:
            # Simulate 100 GB free (100 * 1024^3 bytes)
            mock_usage.return_value = MagicMock(free=100 * (1024**3))
            
            result = get_free_gb("/fake/path")
            
            assert result == 100.0
            mock_usage.assert_called_once_with("/fake/path")

    def test_free_gb_path_not_found(self):
        """Test that get_free_gb raises FileNotFoundError for invalid path."""
        with patch("bin.disk_full_guard.shutil.disk_usage") as mock_usage:
            mock_usage.side_effect = FileNotFoundError("Path not found")
            
            with pytest.raises(FileNotFoundError):
                get_free_gb("/nonexistent/path")

    def test_free_gb_permission_error(self):
        """Test that get_free_gb raises PermissionError for inaccessible path."""
        with patch("bin.disk_full_guard.shutil.disk_usage") as mock_usage:
            mock_usage.side_effect = PermissionError("Access denied")
            
            with pytest.raises(PermissionError):
                get_free_gb("/root/restricted")

    def test_free_gb_handles_generic_exception(self):
        """Test that get_free_gb raises generic exceptions."""
        with patch("bin.disk_full_guard.shutil.disk_usage") as mock_usage:
            mock_usage.side_effect = OSError("Disk error")
            
            with pytest.raises(OSError):
                get_free_gb("/error/path")


class TestWatchLoop:
    """Tests for watch_loop function."""

    @patch("bin.disk_full_guard.get_free_gb")
    @patch("bin.disk_full_guard.os.kill")
    @patch("bin.disk_full_guard.time.sleep")
    def test_free_above_threshold_stays_running(self, mock_sleep, mock_kill, mock_get_free):
        """Test that loop continues when free space is above threshold."""
        # Return high values that stay above threshold
        mock_get_free.return_value = 50.0
        
        # Run with a check that will exit after one iteration via sleep raising
        mock_sleep.side_effect = KeyboardInterrupt()
        
        result = watch_loop("/fake/path", min_gb=10.0, parent_pid=12345, check_interval=0.01)
        
        # Should not have called kill (still has space)
        mock_kill.assert_not_called()
        assert result == 0

    @patch("bin.disk_full_guard.get_free_gb")
    @patch("bin.disk_full_guard.os.kill")
    @patch("bin.disk_full_guard.time.sleep")
    def test_free_below_threshold_sends_sigterm(self, mock_sleep, mock_kill, mock_get_free):
        """Test that loop sends SIGTERM when free space drops below threshold."""
        mock_get_free.return_value = 5.0
        
        result = watch_loop("/fake/path", min_gb=10.0, parent_pid=12345, check_interval=0.01)
        
        # Should have sent SIGTERM
        mock_kill.assert_called_once_with(12345, signal.SIGTERM)
        assert result == 1

    @patch("bin.disk_full_guard.get_free_gb")
    @patch("bin.disk_full_guard.os.kill")
    @patch("bin.disk_full_guard.time.sleep")
    def test_process_not_found_returns_zero(self, mock_sleep, mock_kill, mock_get_free):
        """Test that loop returns 0 if parent process doesn't exist."""
        mock_get_free.return_value = 5.0
        mock_kill.side_effect = ProcessLookupError("Process not found")
        
        result = watch_loop("/fake/path", min_gb=10.0, parent_pid=99999, check_interval=0.01)
        
        mock_kill.assert_called_once()
        assert result == 0

    @patch("bin.disk_full_guard.get_free_gb")
    @patch("bin.disk_full_guard.os.kill")
    @patch("bin.disk_full_guard.time.sleep")
    def test_permission_denied_returns_one(self, mock_sleep, mock_kill, mock_get_free):
        """Test that loop returns 1 if permission denied sending signal."""
        mock_get_free.return_value = 5.0
        mock_kill.side_effect = PermissionError("Permission denied")
        
        result = watch_loop("/fake/path", min_gb=10.0, parent_pid=12345, check_interval=0.01)
        
        mock_kill.assert_called_once()
        assert result == 1

    @patch("bin.disk_full_guard.get_free_gb")
    @patch("bin.disk_full_guard.os.kill")
    @patch("bin.disk_full_guard.time.sleep")
    def test_generic_signal_error_returns_one(self, mock_sleep, mock_kill, mock_get_free):
        """Test that loop returns 1 on generic signal error."""
        mock_get_free.return_value = 5.0
        mock_kill.side_effect = OSError("Signal failed")
        
        result = watch_loop("/fake/path", min_gb=10.0, parent_pid=12345, check_interval=0.01)
        
        mock_kill.assert_called_once()
        assert result == 1

    @patch("bin.disk_full_guard.get_free_gb")
    @patch("bin.disk_full_guard.os.kill")
    @patch("bin.disk_full_guard.time.sleep")
    def test_check_interval_passed_to_sleep(self, mock_sleep, mock_kill, mock_get_free):
        """Test that custom check_interval is passed to sleep."""
        mock_get_free.return_value = 50.0  # Always above threshold
        mock_sleep.side_effect = KeyboardInterrupt()
        
        watch_loop("/fake/path", min_gb=10.0, parent_pid=12345, check_interval=5.0)
        
        # Should have slept with the custom interval
        mock_sleep.assert_called_once_with(5.0)

    @patch("bin.disk_full_guard.get_free_gb")
    @patch("bin.disk_full_guard.os.kill")
    @patch("bin.disk_full_guard.time.sleep")
    def test_logs_free_space_at_debug_level(self, mock_sleep, mock_kill, mock_get_free, caplog):
        """Test that free space is logged at debug level."""
        import logging
        caplog.set_level(logging.DEBUG)
        mock_get_free.return_value = 50.0
        mock_sleep.side_effect = KeyboardInterrupt()
        
        watch_loop("/fake/path", min_gb=10.0, parent_pid=12345, check_interval=0.01)
        
        assert any("Free space: 50.00 GB" in record.message for record in caplog.records)


class TestMain:
    """Tests for main() CLI function."""

    def test_main_path_not_found_returns_one(self):
        """Test main() returns 1 when path doesn't exist."""
        with patch("bin.disk_full_guard.os.path.exists", return_value=False):
            with patch("sys.argv", ["disk_full_guard.py", "--path", "/nonexistent", "--min-gb", "10.0", "--parent-pid", "12345"]):
                result = main()
            
            assert result == 1

    def test_main_invalid_interval_returns_one(self):
        """Test main() returns 1 when --check-interval is not positive."""
        with patch("bin.disk_full_guard.os.path.exists", return_value=True):
            with patch("sys.argv", ["disk_full_guard.py", "--path", "/fake", "--min-gb", "10.0", "--parent-pid", "12345", "--check-interval", "0"]):
                result = main()
            
            assert result == 1

    def test_main_invalid_parent_pid_returns_one(self):
        """Test main() returns 1 when --parent-pid is not positive."""
        with patch("bin.disk_full_guard.os.path.exists", return_value=True):
            with patch("sys.argv", ["disk_full_guard.py", "--path", "/fake", "--min-gb", "10.0", "--parent-pid", "0"]):
                result = main()
            
            assert result == 1

    @patch("bin.disk_full_guard.os.path.exists", return_value=True)
    @patch("bin.disk_full_guard.watch_loop")
    def test_main_all_required_args(self, mock_watch, mock_exists):
        """Test main() with all required arguments."""
        mock_watch.return_value = 0
        
        with patch("sys.argv", ["disk_full_guard.py", "--path", "/fake", "--min-gb", "10.0", "--parent-pid", "12345"]):
            result = main()
        
        assert result == 0
        mock_watch.assert_called_once()
        call_args = mock_watch.call_args
        assert call_args[1]["path"] == "/fake"
        assert call_args[1]["min_gb"] == 10.0
        assert call_args[1]["parent_pid"] == 12345

    @patch("bin.disk_full_guard.os.path.exists", return_value=True)
    @patch("bin.disk_full_guard.watch_loop")
    def test_main_custom_path(self, mock_watch, mock_exists):
        """Test main() with custom --path argument."""
        mock_watch.return_value = 0
        
        with patch("sys.argv", ["disk_full_guard.py", "--path", "/custom/path", "--min-gb", "10.0", "--parent-pid", "12345"]):
            result = main()
        
        assert result == 0
        mock_watch.assert_called_once()
        call_args = mock_watch.call_args
        assert call_args[1]["path"] == "/custom/path"

    @patch("bin.disk_full_guard.os.path.exists", return_value=True)
    @patch("bin.disk_full_guard.watch_loop")
    def test_main_custom_min_gb(self, mock_watch, mock_exists):
        """Test main() with custom --min-gb argument."""
        mock_watch.return_value = 0
        
        with patch("sys.argv", ["disk_full_guard.py", "--path", "/fake", "--min-gb", "5.0", "--parent-pid", "12345"]):
            result = main()
        
        assert result == 0
        mock_watch.assert_called_once()
        call_args = mock_watch.call_args
        assert call_args[1]["min_gb"] == 5.0

    @patch("bin.disk_full_guard.os.path.exists", return_value=True)
    @patch("bin.disk_full_guard.watch_loop")
    def test_main_custom_parent_pid(self, mock_watch, mock_exists):
        """Test main() with custom --parent-pid argument."""
        mock_watch.return_value = 0
        
        with patch("sys.argv", ["disk_full_guard.py", "--path", "/fake", "--min-gb", "10.0", "--parent-pid", "99999"]):
            result = main()
        
        assert result == 0
        mock_watch.assert_called_once()
        call_args = mock_watch.call_args
        assert call_args[1]["parent_pid"] == 99999

    @patch("bin.disk_full_guard.os.path.exists", return_value=True)
    @patch("bin.disk_full_guard.watch_loop")
    def test_main_custom_interval(self, mock_watch, mock_exists):
        """Test main() with custom --check-interval argument."""
        mock_watch.return_value = 0
        
        with patch("sys.argv", ["disk_full_guard.py", "--path", "/fake", "--min-gb", "10.0", "--parent-pid", "12345", "--check-interval", "2.5"]):
            result = main()
        
        assert result == 0
        mock_watch.assert_called_once()
        call_args = mock_watch.call_args
        assert call_args[1]["check_interval"] == 2.5

    @patch("bin.disk_full_guard.os.path.exists", return_value=True)
    @patch("bin.disk_full_guard.watch_loop")
    def test_main_all_args(self, mock_watch, mock_exists):
        """Test main() with all custom arguments."""
        mock_watch.return_value = 0
        
        with patch("sys.argv", [
            "disk_full_guard.py",
            "--path", "/data",
            "--min-gb", "20.0",
            "--parent-pid", "54321",
            "--check-interval", "3.0"
        ]):
            result = main()
        
        assert result == 0
        mock_watch.assert_called_once()
        call_kwargs = mock_watch.call_args[1]
        assert call_kwargs["path"] == "/data"
        assert call_kwargs["min_gb"] == 20.0
        assert call_kwargs["parent_pid"] == 54321
        assert call_kwargs["check_interval"] == 3.0

    @patch("bin.disk_full_guard.os.path.exists", return_value=True)
    @patch("bin.disk_full_guard.watch_loop")
    def test_main_watch_loop_returns_one(self, mock_watch, mock_exists):
        """Test that main() returns 1 when watch_loop returns 1."""
        mock_watch.return_value = 1  # Signal sent
        
        with patch("sys.argv", ["disk_full_guard.py", "--path", "/fake", "--min-gb", "10.0", "--parent-pid", "12345"]):
            result = main()
        
        assert result == 1

    @patch("bin.disk_full_guard.os.path.exists", return_value=True)
    @patch("bin.disk_full_guard.watch_loop")
    def test_main_watch_loop_returns_nonzero(self, mock_watch, mock_exists):
        """Test that main() returns code from watch_loop for other non-zero values."""
        mock_watch.return_value = 2  # Other error
        
        with patch("sys.argv", ["disk_full_guard.py", "--path", "/fake", "--min-gb", "10.0", "--parent-pid", "12345"]):
            result = main()
        
        assert result == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
