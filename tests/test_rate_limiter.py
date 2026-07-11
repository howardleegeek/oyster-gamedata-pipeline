#!/usr/bin/env python3
"""
Tests for recorder_rate_limiter.py
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bin.recorder_rate_limiter import (
    can_record_now,
    count_sessions_today,
    increment_daily_counter,
    load_config,
    reset_daily_counter,
    sum_pending_uploads_gb,
)


class TestRateLimiter:
    """Test suite for rate limiter functionality."""

    def setup_method(self):
        """Set up test environment."""
        # Create temporary directory for testing
        self.temp_dir = tempfile.mkdtemp()
        self.session_dir = Path(self.temp_dir) / "OysterClips"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Create config directory
        self.config_dir = Path(self.temp_dir) / ".oyster"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Patch the global constants to use our temp directory
        self.session_dir_patch = patch("bin.recorder_rate_limiter.SESSION_DIR", self.session_dir)
        self.config_file_patch = patch(
            "bin.recorder_rate_limiter.CONFIG_FILE", self.config_dir / "limits.json"
        )
        self.counter_file_patch = patch(
            "bin.recorder_rate_limiter.DAILY_COUNTER_FILE", self.config_dir / "daily_counter.json"
        )
        self.config_dir_patch = patch("bin.recorder_rate_limiter.CONFIG_DIR", self.config_dir)

        self.session_dir_patch.start()
        self.config_file_patch.start()
        self.counter_file_patch.start()
        self.config_dir_patch.start()

        # Create default config
        self.config = {
            "min_free_gb": 10.0,
            "max_daily_sessions": 50,
            "max_pending_gb": 100.0,
            "auto_delete_after_archive": False,
            "archive_days": 14,
            "delete_after_days": 30,
        }

        with open(self.config_dir / "limits.json", "w") as f:
            json.dump(self.config, f)

    def teardown_method(self):
        """Clean up test environment."""
        self.session_dir_patch.stop()
        self.config_file_patch.stop()
        self.counter_file_patch.stop()
        self.config_dir_patch.stop()

        # Remove temporary directory
        shutil.rmtree(self.temp_dir)

    def test_load_config_defaults(self):
        """Test loading default configuration."""
        # Remove config file to trigger defaults
        (self.config_dir / "limits.json").unlink(missing_ok=True)

        config = load_config()

        assert config["min_free_gb"] == 10.0
        assert config["max_daily_sessions"] == 50
        assert config["max_pending_gb"] == 100.0
        assert config["auto_delete_after_archive"] == False

    def test_load_config_existing(self):
        """Test loading existing configuration."""
        custom_config = {
            "min_free_gb": 5.0,
            "max_daily_sessions": 10,
            "max_pending_gb": 50.0,
            "auto_delete_after_archive": True,
        }

        with open(self.config_dir / "limits.json", "w") as f:
            json.dump(custom_config, f)

        config = load_config()

        assert config["min_free_gb"] == 5.0
        assert config["max_daily_sessions"] == 10
        assert config["max_pending_gb"] == 50.0
        assert config["auto_delete_after_archive"] == True

    def test_count_sessions_today_empty(self):
        """Test counting sessions when none exist."""
        count = count_sessions_today()
        assert count == 0

    def test_count_sessions_today_with_dirs(self):
        """Test counting sessions with directories."""
        # Create some session directories
        today = datetime.now(timezone.utc).date()

        # Create 3 session directories with today's timestamp
        for i in range(3):
            dir_path = self.session_dir / f"clip-session-{i}"
            dir_path.mkdir()
            # Set modification time to today
            timestamp = datetime.combine(today, datetime.min.time()).timestamp()
            os.utime(dir_path, (timestamp, timestamp))

        # Create 2 old session directories
        old_date = today - timedelta(days=1)
        for i in range(2):
            dir_path = self.session_dir / f"clip-old-{i}"
            dir_path.mkdir()
            timestamp = datetime.combine(old_date, datetime.min.time()).timestamp()
            os.utime(dir_path, (timestamp, timestamp))

        count = count_sessions_today()
        assert count == 3

    def test_count_sessions_persisted(self):
        """Test that session count persists across calls."""
        # First call should create counter file
        count_sessions_today()

        # Create a session directory with yesterday's timestamp
        dir_path = self.session_dir / "clip-test"
        dir_path.mkdir()
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        timestamp = datetime.combine(yesterday, datetime.min.time()).timestamp()
        os.utime(dir_path, (timestamp, timestamp))

        # Second call should still return 0 because directory is from yesterday
        count2 = count_sessions_today()
        assert count2 == 0

        # Now increment counter
        increment_daily_counter()

        # Should now show 1
        count3 = count_sessions_today()
        assert count3 == 1

    def test_increment_daily_counter(self):
        """Test incrementing the daily counter."""
        # Initial count should be 0
        count1 = count_sessions_today()
        assert count1 == 0

        # Increment
        increment_daily_counter()

        # Should now be 1
        count2 = count_sessions_today()
        assert count2 == 1

        # Increment again
        increment_daily_counter()

        # Should now be 2
        count3 = count_sessions_today()
        assert count3 == 2

    def test_reset_daily_counter(self):
        """Test resetting the daily counter."""
        # Increment a few times
        increment_daily_counter()
        increment_daily_counter()

        # Should be 2
        assert count_sessions_today() == 2

        # Reset
        reset_daily_counter()

        # Should be 0
        assert count_sessions_today() == 0

    def test_sum_pending_uploads_gb_empty(self):
        """Test calculating pending uploads when none exist."""
        pending_gb = sum_pending_uploads_gb()
        assert pending_gb == 0.0

    def test_sum_pending_uploads_gb_with_files(self):
        """Test calculating pending uploads with files."""
        # Create some uploaded files
        file_sizes = [1024 * 1024 * 100, 1024 * 1024 * 200]  # 100MB and 200MB

        for i, size in enumerate(file_sizes):
            file_path = self.session_dir / f"clip-session-{i}.uploaded.tar.gz"
            with open(file_path, "wb") as f:
                f.write(b"0" * size)

        pending_gb = sum_pending_uploads_gb()
        expected_gb = sum(file_sizes) / 1e9  # 300MB = 0.3GB
        assert abs(pending_gb - expected_gb) < 0.01  # Allow small floating point error

    def test_can_record_now_all_ok(self):
        """Test can_record_now when all conditions are met."""
        with patch("shutil.disk_usage") as mock_disk_usage:
            # Mock plenty of disk space (100GB free)
            mock_disk_usage.return_value.free = 100 * 1e9

            allowed, reason = can_record_now()

            assert allowed == True
            assert reason == "ok"

    def test_can_record_now_low_disk(self):
        """Test can_record_now when disk space is low."""
        with patch("shutil.disk_usage") as mock_disk_usage:
            # Mock low disk space (5GB free, threshold is 10GB)
            mock_disk_usage.return_value.free = 5 * 1e9

            allowed, reason = can_record_now()

            assert allowed == False
            assert "disk free" in reason
            assert "5.0GB" in reason or "5.0" in reason

    def test_can_record_now_daily_quota(self):
        """Test can_record_now when daily quota is reached."""
        with patch("shutil.disk_usage") as mock_disk_usage:
            # Mock plenty of disk space
            mock_disk_usage.return_value.free = 100 * 1e9

            # Mock count_sessions_today to return 50 (the limit)
            with patch("bin.recorder_rate_limiter.count_sessions_today", return_value=50):
                allowed, reason = can_record_now()

                assert allowed == False
                assert "daily quota" in reason
                assert "50/50" in reason

    def test_can_record_now_pending_backlog(self):
        """Test can_record_now when pending upload backlog is too high."""
        with patch("shutil.disk_usage") as mock_disk_usage:
            # Mock plenty of disk space
            mock_disk_usage.return_value.free = 100 * 1e9

            # Mock sum_pending_uploads_gb to return 150GB (over 100GB limit)
            with patch("bin.recorder_rate_limiter.sum_pending_uploads_gb", return_value=150.0):
                allowed, reason = can_record_now()

                assert allowed == False
                assert "upload backlog" in reason
                assert "150.0GB" in reason or "150.0" in reason

    def test_can_record_now_custom_thresholds(self):
        """Test can_record_now with custom thresholds."""
        # Update config with custom thresholds
        custom_config = {"min_free_gb": 20.0, "max_daily_sessions": 5, "max_pending_gb": 50.0}

        with open(self.config_dir / "limits.json", "w") as f:
            json.dump(custom_config, f)

        with patch("shutil.disk_usage") as mock_disk_usage:
            # Mock 15GB free (below 20GB threshold)
            mock_disk_usage.return_value.free = 15 * 1e9

            allowed, reason = can_record_now()

            assert allowed == False
            assert "disk free" in reason
            assert "15.0GB" in reason or "15.0" in reason
            assert "20GB" in reason or "20.0GB" in reason

    def test_daily_counter_resets_at_midnight(self):
        """Test that daily counter resets at midnight UTC."""
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)

        # Set counter to yesterday with count 10
        counter_state = {"date": str(yesterday), "count": 10}
        with open(self.config_dir / "daily_counter.json", "w") as f:
            json.dump(counter_state, f)

        # Count should reset to 0 for today
        count = count_sessions_today()
        assert count == 0

        # Counter file should now have today's date
        with open(self.config_dir / "daily_counter.json", "r") as f:
            updated_state = json.load(f)

        assert updated_state["date"] == str(today)
        assert updated_state["count"] == 0

    def test_daily_counter_persists_restarts(self):
        """Test that daily counter persists across restarts."""
        # First call creates counter
        count_sessions_today()

        # Increment a few times
        increment_daily_counter()
        increment_daily_counter()

        # Check current count
        current_count = count_sessions_today()
        assert current_count == 2

        # Read the counter file directly to verify persistence
        with open(self.config_dir / "daily_counter.json", "r") as f:
            counter_state = json.load(f)

        assert counter_state["count"] == 2

        # Simulate restart by creating a new function call
        # (We can't actually reload the module in the middle of a test easily)
        # Instead, we'll verify the file was written correctly
        assert (self.config_dir / "daily_counter.json").exists()

        # Read file again to ensure it's still 2
        with open(self.config_dir / "daily_counter.json", "r") as f:
            final_state = json.load(f)

        assert final_state["count"] == 2


def test_integration():
    """Integration test for the complete flow."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session_dir = Path(temp_dir) / "OysterClips"
        session_dir.mkdir(parents=True, exist_ok=True)

        config_dir = Path(temp_dir) / ".oyster"
        config_dir.mkdir(parents=True, exist_ok=True)

        # Patch the module
        with (
            patch("bin.recorder_rate_limiter.SESSION_DIR", session_dir),
            patch("bin.recorder_rate_limiter.CONFIG_FILE", config_dir / "limits.json"),
            patch(
                "bin.recorder_rate_limiter.DAILY_COUNTER_FILE", config_dir / "daily_counter.json"
            ),
            patch("bin.recorder_rate_limiter.CONFIG_DIR", config_dir),
        ):

            # Create default config
            config = {"min_free_gb": 10.0, "max_daily_sessions": 50, "max_pending_gb": 100.0}

            with open(config_dir / "limits.json", "w") as f:
                json.dump(config, f)

            # Test with plenty of disk space
            with patch("shutil.disk_usage") as mock_disk_usage:
                mock_disk_usage.return_value.free = 100 * 1e9

                allowed, reason = can_record_now()
                assert allowed == True
                assert reason == "ok"

            # Test increment counter
            increment_daily_counter()
            assert count_sessions_today() == 1

            # Test reset
            reset_daily_counter()
            assert count_sessions_today() == 0


if __name__ == "__main__":
    # Run tests
    import pytest

    pytest.main([__file__, "-v"])
