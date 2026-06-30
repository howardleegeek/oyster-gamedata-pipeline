#!/usr/bin/env python3
"""
Tests for bin/alert_dispatcher.py

Covers main() CLI entry point and core AlertDispatcher functionality.
"""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from bin.alert_dispatcher import Alert, AlertDispatcher, AlertSeverity, load_config


class TestAlert:
    """Tests for Alert class."""

    def test_alert_creation(self):
        """Test Alert object creation with required fields."""
        alert = Alert(
            alert_id="test-001",
            component="test",
            severity=AlertSeverity.WARNING,
            title="Test Alert",
            message="This is a test",
            action="No action needed",
        )
        assert alert.alert_id == "test-001"
        assert alert.component == "test"
        assert alert.severity == AlertSeverity.WARNING
        assert alert.title == "Test Alert"
        assert alert.created_at is not None

    def test_alert_to_dict(self):
        """Test Alert.to_dict() serialization."""
        alert = Alert(
            alert_id="test-002",
            component="disk",
            severity=AlertSeverity.CRITICAL,
            title="Disk Full",
            message="Disk space critically low",
            action="Free up disk space",
        )
        d = alert.to_dict()
        assert d["alert_id"] == "test-002"
        assert d["severity"] == "critical"
        assert d["title"] == "Disk Full"


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_returns_dict(self):
        """Test that load_config returns a dictionary."""
        config = load_config()
        assert isinstance(config, dict)
        # Check expected keys from monitor_thresholds.yaml
        assert "disk_free_min_gb" in config
        assert "upload_backlog_max_gb" in config
        assert "error_rate_per_min" in config


@pytest.fixture
def isolated_config():
    """Provide a config with a fresh temp alerts_file for state isolation."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    )
    tmp.close()
    yield {
        "disk_free_min_gb": 5,
        "upload_backlog_max_gb": 100,
        "error_rate_per_min": 10,
        "health_check_consecutive_failures": 2,
        "daemon_stuck_minutes": 30,
        "alerts_file": tmp.name,
    }
    if os.path.exists(tmp.name):
        os.unlink(tmp.name)


class TestAlertDispatcher:
    """Tests for AlertDispatcher class."""

    def test_dispatcher_creation(self, isolated_config):
        """Test AlertDispatcher can be instantiated."""
        dispatcher = AlertDispatcher(isolated_config)
        assert dispatcher.config == isolated_config

    def test_evaluate_disk_with_free_disk_gb_key(self, isolated_config):
        """Test disk evaluation with correct key 'free_disk_gb'."""
        dispatcher = AlertDispatcher(isolated_config)
        # Disk with 2 GB free (below 5 GB threshold) - use correct key
        disk_metrics = {"free_disk_gb": 2.0, "host": "test-host"}
        alerts = dispatcher.evaluate_disk(disk_metrics)
        # Should trigger alert since 2 < 5 and first time (is_breached=True)
        assert len(alerts) > 0
        assert alerts[0].severity == AlertSeverity.WARNING

    def test_evaluate_disk_ok(self, isolated_config):
        """Test disk evaluation returns no alert when above threshold."""
        dispatcher = AlertDispatcher(isolated_config)
        # Disk with 50 GB free (above 5 GB threshold)
        disk_metrics = {"free_disk_gb": 50.0, "host": "test-host"}
        # is_breached=False on a fresh state, so should_fire returns (False, suppressed)
        alerts = dispatcher.evaluate_disk(disk_metrics)
        assert len(alerts) == 0

    def test_evaluate_disk_missing_key(self, isolated_config):
        """Test disk evaluation handles missing free_disk_gb gracefully."""
        dispatcher = AlertDispatcher(isolated_config)
        # Missing free_disk_gb key
        disk_metrics = {}
        alerts = dispatcher.evaluate_disk(disk_metrics)
        assert len(alerts) == 0

    def test_evaluate_upload_backlog_warning(self, isolated_config):
        """Test upload backlog evaluation returns alert when over threshold."""
        dispatcher = AlertDispatcher(isolated_config)
        # 150 GB backlog (over 100 GB threshold)
        backlog_metrics = {"backlog_gb": 150.0}
        alerts = dispatcher.evaluate_upload_backlog(backlog_metrics)
        assert len(alerts) > 0
        assert alerts[0].severity == AlertSeverity.WARNING

    def test_evaluate_upload_backlog_ok(self, isolated_config):
        """Test upload backlog returns no alert when under threshold."""
        dispatcher = AlertDispatcher(isolated_config)
        # 50 GB backlog (under 100 GB threshold)
        backlog_metrics = {"backlog_gb": 50.0}
        # No alert because is_breached=False
        alerts = dispatcher.evaluate_upload_backlog(backlog_metrics)
        assert len(alerts) == 0

    def test_evaluate_error_rate_warning(self, isolated_config):
        """Test error rate evaluation returns alert when over threshold."""
        dispatcher = AlertDispatcher(isolated_config)
        # 15 errors/min (over 10 threshold)
        error_metrics = {"error_rate_per_min": 15.0}
        alerts = dispatcher.evaluate_error_rate(error_metrics)
        assert len(alerts) > 0
        assert alerts[0].severity == AlertSeverity.WARNING

    def test_evaluate_error_rate_ok(self, isolated_config):
        """Test error rate returns no alert when under threshold."""
        dispatcher = AlertDispatcher(isolated_config)
        # 5 errors/min (under 10 threshold)
        error_metrics = {"error_rate_per_min": 5.0}
        alerts = dispatcher.evaluate_error_rate(error_metrics)
        assert len(alerts) == 0

    def test_evaluate_health_unhealthy(self, isolated_config):
        """Test health check evaluation returns alert on unhealthy status."""
        dispatcher = AlertDispatcher(isolated_config)
        # Health check with unhealthy status
        health_metrics = {"test-api": {"status": "unhealthy", "error": "timeout"}}
        alerts = dispatcher.evaluate_health(health_metrics)
        # First failure doesn't meet threshold yet (need 2)
        assert len(alerts) == 0

    def test_evaluate_health_healthy(self, isolated_config):
        """Test health check returns no alert when healthy."""
        dispatcher = AlertDispatcher(isolated_config)
        # 0 consecutive failures - healthy
        health_metrics = {"test-api": {"status": "healthy"}}
        alerts = dispatcher.evaluate_health(health_metrics)
        assert len(alerts) == 0


class TestMain:
    """Tests for main() CLI entry point."""

    @patch("bin.alert_dispatcher.load_config")
    @patch("bin.alert_dispatcher.AlertDispatcher")
    def test_main_with_valid_file(self, mock_dispatcher_class, mock_load_config):
        """Test main() with a valid metrics file argument."""
        # Setup mocks
        mock_config = {
            "disk_free_min_gb": 5,
            "upload_backlog_max_gb": 100,
            "error_rate_per_min": 10,
            "health_check_consecutive_failures": 2,
            "daemon_stuck_minutes": 30,
        }
        mock_load_config.return_value = mock_config

        mock_dispatcher = MagicMock()
        mock_dispatcher_class.return_value = mock_dispatcher
        mock_dispatcher.process_metrics.return_value = []

        # Create temp file with metrics
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            metrics = {"disk": {"free_disk_gb": 2.0, "host": "test"}}
            f.write(json.dumps(metrics) + "\n")
            temp_path = f.name

        try:
            # Run main with file argument
            with patch.object(sys, "argv", ["alert_dispatcher.py", temp_path]):
                from bin.alert_dispatcher import main
                main()

            # Verify process_metrics was called
            mock_dispatcher.process_metrics.assert_called()
        finally:
            os.unlink(temp_path)

    @patch("bin.alert_dispatcher.load_config")
    def test_main_with_invalid_file(self, mock_load_config):
        """Test main() exits with code 1 when file does not exist."""
        mock_config = {
            "disk_free_min_gb": 5,
            "upload_backlog_max_gb": 100,
            "error_rate_per_min": 10,
            "health_check_consecutive_failures": 2,
            "daemon_stuck_minutes": 30,
        }
        mock_load_config.return_value = mock_config

        nonexistent_file = "/nonexistent/path/metrics.jsonl"

        with patch.object(sys, "argv", ["alert_dispatcher.py", nonexistent_file]):
            from bin.alert_dispatcher import main
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
