"""
test_alert_dispatcher.py — Tests for alert_dispatcher.py

Tests:
  - Verify alert fires on threshold breach
  - Verify auto-clear on recovery
  - Verify webhook payload format
  - Verify dedup (don't spam same alert every 60s; only re-alert if escalating)
"""

import json
import os
import sys
import time
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add bin directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

from alert_dispatcher import (
    Alert,
    AlertSeverity,
    AlertStateManager,
    WebhookSender,
    AlertDispatcher,
)


class TestAlert(unittest.TestCase):
    """Test Alert class formatting."""

    def test_alert_creation(self):
        alert = Alert(
            alert_id="abc123",
            component="depth_endpoint",
            severity=AlertSeverity.CRITICAL,
            title="depth_endpoint health check failed",
            message="Health check failed 2 consecutive times.",
            action="Check Modal app status",
            last_success="2 minutes ago",
            last_error="ConnectionError on https://oyster-depth.modal.run/health",
            recent_samples="3/5 failures",
        )
        self.assertEqual(alert.alert_id, "abc123")
        self.assertEqual(alert.component, "depth_endpoint")
        self.assertEqual(alert.severity, AlertSeverity.CRITICAL)

    def test_slack_payload_format(self):
        alert = Alert(
            alert_id="abc123",
            component="depth_endpoint",
            severity=AlertSeverity.CRITICAL,
            title="depth_endpoint health check failed",
            message="Health check failed.",
            action="Check Modal app status",
            last_success="2 minutes ago",
            last_error="ConnectionError",
            recent_samples="3/5 failures",
        )
        payload = alert.format_slack()

        self.assertIn("text", payload)
        self.assertIn("🔴", payload["text"])
        self.assertIn("oyster-prod", payload["text"])
        self.assertIn("blocks", payload)
        self.assertEqual(len(payload["blocks"]), 2)

    def test_discord_payload_format(self):
        alert = Alert(
            alert_id="abc123",
            component="depth_endpoint",
            severity=AlertSeverity.CRITICAL,
            title="depth_endpoint health check failed",
            message="Health check failed.",
            action="Check Modal app status",
            last_success="2 minutes ago",
            last_error="ConnectionError",
            recent_samples="3/5 failures",
        )
        payload = alert.format_discord()

        self.assertIn("embeds", payload)
        self.assertEqual(len(payload["embeds"]), 1)
        embed = payload["embeds"][0]
        self.assertIn("oyster-prod", embed["title"])
        self.assertEqual(embed["color"], 0xFF0000)  # Red for critical
        self.assertIn("timestamp", embed)

    def test_warning_severity_color(self):
        alert = Alert(
            alert_id="abc123",
            component="upload_backlog",
            severity=AlertSeverity.WARNING,
            title="Upload backlog exceeds threshold",
            message="Backlog is 150 GB.",
            action="Check upload daemon.",
        )
        payload = alert.format_discord()
        self.assertEqual(payload["embeds"][0]["color"], 0xFFA500)  # Orange for warning

    def test_pagerduty_payload_format(self):
        alert = Alert(
            alert_id="abc123",
            component="depth_endpoint",
            severity=AlertSeverity.CRITICAL,
            title="depth_endpoint health check failed",
            message="Health check failed.",
            action="Check Modal app status",
        )
        with patch.dict(os.environ, {"PAGERDUTY_INTEGRATION_KEY": "test-key"}):
            payload = alert.format_pagerduty()

        self.assertEqual(payload["routing_key"], "test-key")
        self.assertEqual(payload["event_action"], "trigger")
        self.assertEqual(payload["payload"]["severity"], "critical")
        self.assertEqual(payload["payload"]["source"], "oyster-depth_endpoint")


class TestAlertStateManager(unittest.TestCase):
    """Test alert deduplication and escalation logic."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.alerts_file = os.path.join(self.temp_dir, "alerts.jsonl")
        self.state_manager = AlertStateManager(
            dedup_window_seconds=60,
            escalation_seconds=300,
            alerts_file=self.alerts_file,
        )

    def test_initial_alert_fires(self):
        """First alert should fire immediately."""
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=True)
        self.assertTrue(should_fire)
        self.assertEqual(fire_type, "initial")

    def test_dedup_within_window(self):
        """Same alert within dedup window should not fire."""
        # Fire initial
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=True)
        self.assertTrue(should_fire)
        self.state_manager.record_fire("alert1", fire_type)

        # Try again immediately — should be suppressed
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=True)
        self.assertFalse(should_fire)
        self.assertEqual(fire_type, "suppressed")

    def test_escalation_after_window(self):
        """Alert should escalate after escalation window."""
        # Fire initial
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=True)
        self.assertTrue(should_fire)
        self.state_manager.record_fire("alert1", fire_type)

        # Simulate time passing past escalation window
        self.state_manager.state["alert1"]["last_escalated"] = time.time() - 301
        self.state_manager.state["alert1"]["last_fired"] = time.time() - 301

        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=True)
        self.assertTrue(should_fire)
        self.assertEqual(fire_type, "escalation")

    def test_auto_clear_on_recovery(self):
        """Alert should fire as 'cleared' when condition recovers."""
        # Fire initial
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=True)
        self.assertTrue(should_fire)
        self.state_manager.record_fire("alert1", fire_type)

        # Now condition is resolved
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=False)
        self.assertTrue(should_fire)
        self.assertEqual(fire_type, "cleared")

    def test_re_fire_after_clear(self):
        """Alert should fire as 'initial' again after being cleared and re-triggered."""
        # Fire initial
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=True)
        self.assertTrue(should_fire)
        self.state_manager.record_fire("alert1", fire_type)

        # Clear
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=False)
        self.assertTrue(should_fire)
        self.state_manager.record_fire("alert1", fire_type)

        # Re-trigger — should be initial again
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=True)
        self.assertTrue(should_fire)
        self.assertEqual(fire_type, "initial")

    def test_no_fire_for_inactive_unknown_alert(self):
        """Unknown alert that is not active should not fire."""
        should_fire, fire_type = self.state_manager.should_fire("unknown", is_active=False)
        self.assertFalse(should_fire)

    def test_state_persistence(self):
        """State should be saved to and loaded from file."""
        should_fire, fire_type = self.state_manager.should_fire("alert1", is_active=True)
        self.assertTrue(should_fire)
        self.state_manager.record_fire("alert1", fire_type)

        # Create new state manager with same file
        new_sm = AlertStateManager(
            dedup_window_seconds=60,
            escalation_seconds=300,
            alerts_file=self.alerts_file,
        )
        new_sm._load_state()

        self.assertIn("alert1", new_sm.state)
        self.assertTrue(new_sm.state["alert1"]["cleared"] is False)


class TestAlertDispatcher(unittest.TestCase):
    """Test the main AlertDispatcher class."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.alerts_file = os.path.join(self.temp_dir, "alerts.jsonl")
        self.config = {
            "disk_free_min_gb": 5,
            "upload_backlog_max_gb": 100,
            "error_rate_per_min": 10,
            "health_check_consecutive_failures": 2,
            "daemon_stuck_minutes": 30,
            "alert_dedup_window_seconds": 60,
            "alert_escalation_seconds": 300,
            "alerts_file": self.alerts_file,
        }
        self.dispatcher = AlertDispatcher(self.config)

    def _make_health_metrics(self, healthy: bool = True, error: str = None) -> dict:
        """Create a health check result dict."""
        return {
            "depth_endpoint": {
                "name": "depth_endpoint",
                "url": "https://oyster-depth.modal.run/health",
                "status": "healthy" if healthy else "unhealthy",
                "status_code": 200 if healthy else None,
                "response_time_ms": 150.0 if healthy else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": error,
            }
        }

    def test_health_alert_fires_on_consecutive_failures(self):
        """Alert should fire after 2 consecutive health check failures."""
        # First failure — should not fire yet (threshold is 2)
        metrics = {"health": self._make_health_metrics(healthy=False, error="ConnectionError")}
        alerts = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts), 0)

        # Second failure — should fire
        metrics = {"health": self._make_health_metrics(healthy=False, error="ConnectionError")}
        alerts = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts), 1)
        self.assertIn("depth_endpoint", alerts[0].title)
        self.assertIn("health check failed", alerts[0].title)

    def test_health_alert_auto_clears_on_recovery(self):
        """Alert should auto-clear when health check recovers."""
        # Two failures to trigger alert
        for _ in range(2):
            metrics = {"health": self._make_health_metrics(healthy=False, error="ConnectionError")}
            self.dispatcher.process_metrics(metrics)

        # Recovery
        metrics = {"health": self._make_health_metrics(healthy=True)}
        alerts = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts), 1)
        self.assertIn("recovered", alerts[0].title)
        self.assertEqual(alerts[0].severity, AlertSeverity.INFO)

    def test_upload_backlog_alert(self):
        """Alert should fire when upload backlog exceeds threshold."""
        metrics = {
            "upload_backlog": {
                "backlog_gb": 150.0,
                "backlog_bytes": 150 * 1024**3,
                "file_count": 500,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
        alerts = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts), 1)
        self.assertIn("Upload backlog", alerts[0].title)

    def test_upload_backlog_no_alert_under_threshold(self):
        """No alert when backlog is under threshold."""
        metrics = {
            "upload_backlog": {
                "backlog_gb": 50.0,
                "backlog_bytes": 50 * 1024**3,
                "file_count": 100,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
        alerts = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts), 0)

    def test_error_rate_alert(self):
        """Alert should fire when error rate exceeds threshold."""
        metrics = {
            "error_rate": {
                "error_count": 50,
                "error_rate_per_min": 15.0,
                "window_minutes": 5,
                "sample_errors": ["ERROR: something broke"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
        alerts = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts), 1)
        self.assertIn("Error rate", alerts[0].title)

    def test_disk_space_alert(self):
        """Alert should fire when disk free is below threshold."""
        metrics = {
            "disk": {
                "status": "ok",
                "free_disk_gb": 3,
                "host": "minipc1",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
        alerts = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts), 1)
        self.assertIn("Disk space", alerts[0].title)
        self.assertIn("minipc1", alerts[0].title)

    def test_disk_space_no_alert_when_unknown(self):
        """No alert when disk space cannot be determined."""
        metrics = {
            "disk": {
                "status": "error",
                "free_disk_gb": None,
                "host": "minipc1",
                "error": "SSH failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
        alerts = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts), 0)

    def test_daemon_stuck_alert(self):
        """Alert should fire when daemon is stuck."""
        metrics = {
            "daemon_stuck": {
                "status": "stuck",
                "daemon_state": "RECORDING",
                "state_age_minutes": 45.0,
                "game_state_growing": False,
                "is_stuck": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
        alerts = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts), 1)
        self.assertIn("stuck", alerts[0].title)

    def test_dedup_no_spam(self):
        """Same alert should not fire twice within dedup window."""
        # Trigger alert
        metrics = {
            "upload_backlog": {
                "backlog_gb": 150.0,
                "backlog_bytes": 150 * 1024**3,
                "file_count": 500,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
        alerts1 = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts1), 1)

        # Same metrics again — should be deduped
        alerts2 = self.dispatcher.process_metrics(metrics)
        self.assertEqual(len(alerts2), 0)

    def test_multiple_alerts_in_single_poll(self):
        """Multiple threshold breaches should generate multiple alerts."""
        metrics = {
            "health": self._make_health_metrics(healthy=False, error="ConnectionError"),
            "upload_backlog": {
                "backlog_gb": 150.0,
                "backlog_bytes": 150 * 1024**3,
                "file_count": 500,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "error_rate": {
                "error_count": 50,
                "error_rate_per_min": 15.0,
                "window_minutes": 5,
                "sample_errors": ["ERROR: something broke"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "disk": {
                "status": "ok",
                "free_disk_gb": 3,
                "host": "minipc1",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "daemon_stuck": {
                "status": "stuck",
                "daemon_state": "RECORDING",
                "state_age_minutes": 45.0,
                "game_state_growing": False,
                "is_stuck": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
        # First poll — health needs 2 consecutive failures
        alerts = self.dispatcher.process_metrics(metrics)
        # Should have backlog, error_rate, disk, daemon_stuck = 4 alerts
        self.assertEqual(len(alerts), 4)

    def test_webhook_payload_contains_required_fields(self):
        """Webhook payloads should contain all required fields."""
        alert = Alert(
            alert_id="test123",
            component="depth_endpoint",
            severity=AlertSeverity.CRITICAL,
            title="depth_endpoint health check failed",
            message="Health check failed 2 consecutive times.",
            action="Check Modal app status",
            last_success="2 minutes ago",
            last_error="ConnectionError on https://oyster-depth.modal.run/health",
            recent_samples="3/5 failures",
        )

        # Slack
        slack_payload = alert.format_slack()
        self.assertIn("text", slack_payload)
        self.assertIn("blocks", slack_payload)
        self.assertIn("oyster-prod", slack_payload["text"])

        # Discord
        discord_payload = alert.format_discord()
        self.assertIn("embeds", discord_payload)
        embed = discord_payload["embeds"][0]
        self.assertIn("title", embed)
        self.assertIn("description", embed)
        self.assertIn("color", embed)
        self.assertIn("timestamp", embed)
        self.assertIn("footer", embed)


class TestWebhookSender(unittest.TestCase):
    """Test webhook sending with mocking."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.queue_file = os.path.join(self.temp_dir, "alert_queue.json")

    def _make_sender(self, env_vars=None):
        """Create a WebhookSender with isolated queue file."""
        if env_vars:
            self.env_patcher = patch.dict(os.environ, env_vars)
            self.env_patcher.start()
        else:
            self.env_patcher = None

        sender = WebhookSender()
        # Override queue file to isolated temp dir
        sender.queue_file = self.queue_file
        sender.offline_queue = []
        return sender

    def tearDown(self):
        if self.env_patcher:
            self.env_patcher.stop()

    @patch("alert_dispatcher.requests.post")
    def test_slack_send_success(self, mock_post):
        """Slack send should succeed with 200 response."""
        mock_post.return_value = MagicMock(status_code=200)

        alert = Alert(
            alert_id="test123",
            component="test",
            severity=AlertSeverity.CRITICAL,
            title="Test alert",
            message="Test message",
            action="Test action",
        )

        sender = self._make_sender({"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"})
        result = sender.send_slack(alert)

        self.assertTrue(result)
        mock_post.assert_called_once()

    @patch("alert_dispatcher.requests.post")
    def test_slack_send_failure_queues(self, mock_post):
        """Slack send failure should queue the alert."""
        import requests
        mock_post.side_effect = requests.exceptions.RequestException("Network error")

        alert = Alert(
            alert_id="test123",
            component="test",
            severity=AlertSeverity.CRITICAL,
            title="Test alert",
            message="Test message",
            action="Test action",
        )

        sender = self._make_sender({"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"})
        result = sender.send_slack(alert)

        self.assertFalse(result)
        self.assertEqual(len(sender.offline_queue), 1)
        self.assertEqual(sender.offline_queue[0]["channel"], "slack")

    @patch("alert_dispatcher.requests.post")
    def test_discord_send_success(self, mock_post):
        """Discord send should succeed with 200/204 response."""
        mock_post.return_value = MagicMock(status_code=204)

        alert = Alert(
            alert_id="test123",
            component="test",
            severity=AlertSeverity.CRITICAL,
            title="Test alert",
            message="Test message",
            action="Test action",
        )

        sender = self._make_sender({"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test"})
        result = sender.send_discord(alert)

        self.assertTrue(result)

    def test_no_send_without_config(self):
        """Should not send if webhook URLs are not configured."""
        alert = Alert(
            alert_id="test123",
            component="test",
            severity=AlertSeverity.CRITICAL,
            title="Test alert",
            message="Test message",
            action="Test action",
        )

        sender = self._make_sender({})
        result_slack = sender.send_slack(alert)
        result_discord = sender.send_discord(alert)
        result_pd = sender.send_pagerduty(alert)

        self.assertFalse(result_slack)
        self.assertFalse(result_discord)
        self.assertFalse(result_pd)


if __name__ == "__main__":
    unittest.main()
