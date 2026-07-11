#!/usr/bin/env python3
"""
alert_dispatcher.py — Triggers alerts when thresholds are breached.

Alert conditions:
  - 2 consecutive health checks fail (auto-cleared on recovery)
  - Upload backlog > 100 GB
  - Recent error rate > 10 errors/min
  - Disk free < 5 GB
  - Daemon stuck in same state > 30 min

Sends to:
  - Slack webhook (configurable URL via SLACK_WEBHOOK_URL env var)
  - Discord webhook (configurable URL via DISCORD_WEBHOOK_URL env var)
  - Optional: PagerDuty (via PAGERDUTY_INTEGRATION_KEY env var)

Features:
  - Alert dedup: same alert in 60s window only fires once
  - Escalation: re-alert after 5 min if still failing
  - Auto-clear on recovery
  - Local-first: queues alerts if network is down
"""

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests
import yaml

# ---------------------------------------------------------------------------
# Paths & Config
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "monitor_thresholds.yaml")

def load_config() -> dict:
    """Load thresholds config from YAML."""
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

def expand_oyster_path(path: str) -> str:
    """Expand ~ in oyster paths."""
    return os.path.expanduser(path)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("alert_dispatcher")

# ---------------------------------------------------------------------------
# Alert Types
# ---------------------------------------------------------------------------

class AlertSeverity:
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Alert:
    """Represents a single alert."""

    def __init__(
        self,
        alert_id: str,
        component: str,
        severity: str,
        title: str,
        message: str,
        action: str,
        last_success: Optional[str] = None,
        last_error: Optional[str] = None,
        recent_samples: Optional[str] = None,
    ):
        self.alert_id = alert_id
        self.component = component
        self.severity = severity
        self.title = title
        self.message = message
        self.action = action
        self.last_success = last_success
        self.last_error = last_error
        self.recent_samples = recent_samples
        self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "component": self.component,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "action": self.action,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "recent_samples": self.recent_samples,
            "created_at": self.created_at,
        }

    def format_slack(self) -> dict:
        """Format for Slack webhook."""
        emoji = (
            "🔴" if self.severity == AlertSeverity.CRITICAL
            else "🟡" if self.severity == AlertSeverity.WARNING
            else "🔵"
        )
        return {
            "text": f"{emoji} [oyster-prod] {self.title}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{emoji} [oyster-prod] {self.title}*\n{self.message}"
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Component:*\n{self.component}"},
                        {"type": "mrkdwn", "text": f"*Severity:*\n{self.severity}"},
                    ]
                },
            ],
        }

    def format_discord(self) -> dict:
        """Format for Discord webhook."""
        color = (
            0xFF0000 if self.severity == AlertSeverity.CRITICAL
            else 0xFFA500 if self.severity == AlertSeverity.WARNING
            else 0x0000FF
        )
        fields = []
        if self.last_success:
            fields.append({"name": "Last success", "value": self.last_success, "inline": True})
        if self.last_error:
            fields.append({"name": "Last error", "value": self.last_error, "inline": True})
        if self.recent_samples:
            fields.append({"name": "Recent samples", "value": self.recent_samples, "inline": True})
        fields.append({"name": "Action", "value": self.action, "inline": False})

        return {
            "content": None,
            "embeds": [{
                "title": f"[oyster-prod] {self.title}",
                "description": self.message,
                "color": color,
                "fields": fields,
                "timestamp": self.created_at,
                "footer": {"text": "Oyster Monitor"},
            }]
        }

    def format_pagerduty(self) -> dict:
        """Format for PagerDuty Events API v2."""
        return {
            "routing_key": os.environ.get("PAGERDUTY_INTEGRATION_KEY", ""),
            "event_action": "trigger",
            "payload": {
                "summary": f"[oyster-prod] {self.title}",
                "severity": "critical" if self.severity == AlertSeverity.CRITICAL else "warning",
                "source": f"oyster-{self.component}",
                "custom_details": {
                    "message": self.message,
                    "action": self.action,
                    "last_success": self.last_success,
                    "last_error": self.last_error,
                    "recent_samples": self.recent_samples,
                },
            },
        }


# ---------------------------------------------------------------------------
# Alert State Manager (dedup + escalation)
# ---------------------------------------------------------------------------

class AlertStateManager:
    """Manages alert state for deduplication and escalation."""

    def __init__(
        self,
        dedup_window_seconds: int = 60,
        escalation_seconds: int = 300,
        alerts_file: Optional[str] = None,
    ):
        self.dedup_window = dedup_window_seconds
        self.escalation_seconds = escalation_seconds
        default_alerts_file = "~/.oyster/monitor_alerts.jsonl"
        self.alerts_file = expand_oyster_path(alerts_file) if alerts_file else default_alerts_file

        # In-memory state: alert_id -> {last_fired, fire_count, last_escalated, cleared}
        self.state: dict[str, dict] = {}

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.alerts_file), exist_ok=True)

    def _load_state(self):
        """Load state from alerts file."""
        if os.path.exists(self.alerts_file):
            try:
                with open(self.alerts_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entry = json.loads(line)
                            aid = entry.get("alert_id")
                            if aid:
                                self.state[aid] = entry
            except (json.JSONDecodeError, IOError) as e:
                log.debug("Failed to parse alerts state file: %s", e)

    def _save_state(self, alert_id: str, entry: dict):
        """Append state entry to alerts file."""
        with open(self.alerts_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def should_fire(self, alert_id: str, is_active: bool) -> tuple[bool, str]:
        """
        Determine if an alert should fire.

        Returns:
            (should_fire, fire_type) where fire_type is one of:
                "initial" — first time firing
                "escalation" — still failing after escalation window
                "cleared" — recovery detected
                "suppressed" — within dedup window, don't fire
        """
        now = time.time()

        if alert_id not in self.state:
            if is_active:
                return True, "initial"
            return False, "suppressed"

        state_entry = self.state[alert_id]
        last_fired = state_entry.get("last_fired", 0)
        is_cleared = state_entry.get("cleared", False)
        last_escalated = state_entry.get("last_escalated", 0)

        if is_active:
            if is_cleared:
                # Was cleared, now failing again — fire as initial
                return True, "initial"

            time_since_fired = now - last_fired
            if time_since_fired < self.dedup_window:
                return False, "suppressed"

            # Check escalation
            time_since_escalated = now - last_escalated
            if time_since_escalated >= self.escalation_seconds:
                return True, "escalation"

            return False, "suppressed"
        else:
            # Not active — clear if it was previously active
            if not is_cleared:
                return True, "cleared"
            return False, "suppressed"

    def record_fire(self, alert_id: str, fire_type: str):
        """Record that an alert was fired."""
        now = time.time()
        entry = {
            "alert_id": alert_id,
            "last_fired": now,
            "fire_count": self.state.get(alert_id, {}).get("fire_count", 0) + 1,
            "last_escalated": now if fire_type == "escalation" else self.state.get(alert_id, {}).get("last_escalated", now),
            "cleared": fire_type == "cleared",
            "fire_type": fire_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.state[alert_id] = entry
        self._save_state(alert_id, entry)


# ---------------------------------------------------------------------------
# Webhook Senders
# ---------------------------------------------------------------------------

class WebhookSender:
    """Sends alerts to Slack, Discord, and PagerDuty."""

    def __init__(self):
        self.slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
        self.discord_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.pagerduty_key = os.environ.get("PAGERDUTY_INTEGRATION_KEY", "")

        # Queue for offline alerts
        self.offline_queue: list[dict] = []
        self.queue_file = expand_oyster_path("~/.oyster/alert_queue.json")
        os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
        self._load_queue()

    def _load_queue(self):
        """Load queued alerts from disk."""
        if os.path.exists(self.queue_file):
            try:
                with open(self.queue_file, "r") as f:
                    self.offline_queue = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                log.debug("Failed to parse alerts queue file: %s", e)
                self.offline_queue = []

    def _save_queue(self):
        """Save queued alerts to disk."""
        with open(self.queue_file, "w") as f:
            json.dump(self.offline_queue, f)

    def send_slack(self, alert: Alert) -> bool:
        """Send alert to Slack webhook."""
        if not self.slack_url:
            log.debug("Slack webhook URL not configured, skipping")
            return False

        payload = alert.format_slack()
        try:
            resp = requests.post(self.slack_url, json=payload, timeout=10)
            if resp.status_code == 200:
                log.info(f"Slack alert sent: {alert.title}")
                return True
            else:
                log.error(f"Slack webhook returned {resp.status_code}: {resp.text}")
                return False
        except requests.exceptions.RequestException as e:
            log.error(f"Failed to send Slack alert: {e}")
            self.offline_queue.append({"channel": "slack", "payload": payload, "timestamp": time.time()})
            self._save_queue()
            return False

    def send_discord(self, alert: Alert) -> bool:
        """Send alert to Discord webhook."""
        if not self.discord_url:
            log.debug("Discord webhook URL not configured, skipping")
            return False

        payload = alert.format_discord()
        try:
            resp = requests.post(self.discord_url, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                log.info(f"Discord alert sent: {alert.title}")
                return True
            else:
                log.error(f"Discord webhook returned {resp.status_code}: {resp.text}")
                return False
        except requests.exceptions.RequestException as e:
            log.error(f"Failed to send Discord alert: {e}")
            self.offline_queue.append({"channel": "discord", "payload": payload, "timestamp": time.time()})
            self._save_queue()
            return False

    def send_pagerduty(self, alert: Alert) -> bool:
        """Send alert to PagerDuty."""
        if not self.pagerduty_key:
            log.debug("PagerDuty integration key not configured, skipping")
            return False

        payload = alert.format_pagerduty()
        try:
            resp = requests.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=payload,
                timeout=10,
            )
            if resp.status_code == 202:
                log.info(f"PagerDuty alert sent: {alert.title}")
                return True
            else:
                log.error(f"PagerDuty returned {resp.status_code}: {resp.text}")
                return False
        except requests.exceptions.RequestException as e:
            log.error(f"Failed to send PagerDuty alert: {e}")
            return False

    def send_all(self, alert: Alert):
        """Send alert to all configured channels."""
        self.send_slack(alert)
        self.send_discord(alert)
        self.send_pagerduty(alert)

    def flush_queue(self):
        """Try to send queued alerts."""
        if not self.offline_queue:
            return

        remaining = []
        for item in self.offline_queue:
            channel = item["channel"]
            payload = item["payload"]
            try:
                if channel == "slack" and self.slack_url:
                    resp = requests.post(self.slack_url, json=payload, timeout=10)
                    if resp.status_code == 200:
                        log.info("Flushed queued Slack alert")
                        continue
                elif channel == "discord" and self.discord_url:
                    resp = requests.post(self.discord_url, json=payload, timeout=10)
                    if resp.status_code in (200, 204):
                        log.info("Flushed queued Discord alert")
                        continue
            except requests.exceptions.RequestException as e:
                log.debug("Failed to flush queued alert: %s", e)
            remaining.append(item)

        self.offline_queue = remaining
        self._save_queue()


# ---------------------------------------------------------------------------
# Alert Dispatcher
# ---------------------------------------------------------------------------

class AlertDispatcher:
    """Main alert dispatcher — evaluates metrics and fires alerts."""

    def __init__(self, config: dict):
        self.config = config
        self.thresholds = {
            "disk_free_min_gb": config.get("disk_free_min_gb", 5),
            "upload_backlog_max_gb": config.get("upload_backlog_max_gb", 100),
            "error_rate_per_min": config.get("error_rate_per_min", 10),
            "health_check_consecutive_failures": config.get("health_check_consecutive_failures", 2),
            "daemon_stuck_minutes": config.get("daemon_stuck_minutes", 30),
        }

        self.state_manager = AlertStateManager(
            dedup_window_seconds=config.get("alert_dedup_window_seconds", 60),
            escalation_seconds=config.get("alert_escalation_seconds", 300),
            alerts_file=config.get("alerts_file", "~/.oyster/monitor_alerts.jsonl"),
        )
        self.state_manager._load_state()

        self.webhook_sender = WebhookSender()

        # Track consecutive health check failures per endpoint
        self.consecutive_failures: dict[str, int] = {}
        self.last_success_time: dict[str, float] = {}
        self.last_error_msg: dict[str, str] = {}

    def _make_alert_id(self, component: str, alert_type: str) -> str:
        """Generate a unique alert ID."""
        raw = f"{component}:{alert_type}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _time_ago(self, timestamp_str: Optional[str]) -> str:
        """Format a timestamp as 'X minutes ago'."""
        if not timestamp_str:
            return "never"
        try:
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            diff = (now - ts).total_seconds()
            if diff < 60:
                return f"{int(diff)} seconds ago"
            elif diff < 3600:
                return f"{int(diff / 60)} minutes ago"
            else:
                return f"{int(diff / 3600)} hours ago"
        except (ValueError, TypeError) as exc:
            log.debug("_time_ago failed to parse %s: %s", timestamp_str, exc)
            return "unknown"

    def evaluate_health(self, health_results: dict[str, dict]):
        """Evaluate health check results and generate alerts."""
        alerts = []

        for name, result in health_results.items():
            is_healthy = result.get("status") == "healthy"
            alert_id = self._make_alert_id(name, "health_check")

            if is_healthy:
                self.consecutive_failures[name] = 0
                self.last_success_time[name] = time.time()
                self.last_error_msg[name] = None

                should_fire, fire_type = self.state_manager.should_fire(alert_id, is_active=False)
                if should_fire and fire_type == "cleared":
                    self.state_manager.record_fire(alert_id, fire_type)
                    alert = Alert(
                        alert_id=alert_id,
                        component=name,
                        severity=AlertSeverity.INFO,
                        title=f"{name} health check recovered",
                        message=f"{name} is now healthy after previous failures.",
                        action="No action needed — auto-cleared.",
                    )
                    alerts.append(alert)
            else:
                self.consecutive_failures[name] = self.consecutive_failures.get(name, 0) + 1
                self.last_error_msg[name] = result.get("error", "Unknown error")

                failures = self.consecutive_failures[name]
                threshold = self.thresholds["health_check_consecutive_failures"]

                if failures >= threshold:
                    should_fire, fire_type = self.state_manager.should_fire(alert_id, is_active=True)
                    if should_fire:
                        self.state_manager.record_fire(alert_id, fire_type)

                        last_success = self._time_ago(
                            result.get("timestamp") if self.last_success_time.get(name) else None
                        )
                        if self.last_success_time.get(name):
                            last_success = self._time_ago(
                                datetime.fromtimestamp(self.last_success_time[name], tz=timezone.utc).isoformat()
                            )

                        severity = AlertSeverity.CRITICAL if fire_type == "escalation" else AlertSeverity.WARNING
                        title_suffix = "still failing" if fire_type == "escalation" else "health check failed"

                        alert = Alert(
                            alert_id=alert_id,
                            component=name,
                            severity=severity,
                            title=f"{name} {title_suffix}",
                            message=f"{name} health check has failed {failures} consecutive times (threshold: {threshold}).",
                            action=f"Check {name} service status and logs.",
                            last_success=last_success,
                            last_error=self.last_error_msg.get(name, "Unknown"),
                            recent_samples=f"{failures}/{failures} failures",
                        )
                        alerts.append(alert)

        return alerts

    def evaluate_upload_backlog(self, backlog: dict):
        """Evaluate upload backlog size."""
        alerts = []
        backlog_gb = backlog.get("backlog_gb", 0)
        threshold = self.thresholds["upload_backlog_max_gb"]
        alert_id = self._make_alert_id("upload_backlog", "size")

        is_breached = backlog_gb > threshold
        should_fire, fire_type = self.state_manager.should_fire(alert_id, is_active=is_breached)

        if should_fire:
            self.state_manager.record_fire(alert_id, fire_type)

            if fire_type == "cleared":
                alert = Alert(
                    alert_id=alert_id,
                    component="upload_backlog",
                    severity=AlertSeverity.INFO,
                    title="Upload backlog recovered",
                    message=f"Upload backlog is now {backlog_gb} GB (threshold: {threshold} GB).",
                    action="No action needed — auto-cleared.",
                )
            else:
                severity = AlertSeverity.CRITICAL if fire_type == "escalation" else AlertSeverity.WARNING
                title_suffix = "still exceeding threshold" if fire_type == "escalation" else "exceeds threshold"
                alert = Alert(
                    alert_id=alert_id,
                    component="upload_backlog",
                    severity=severity,
                    title=f"Upload backlog {title_suffix}",
                    message=f"Upload backlog is {backlog_gb} GB (threshold: {threshold} GB). {backlog.get('file_count', 0)} files pending.",
                    action="Check upload daemon and network connectivity.",
                    recent_samples=f"{backlog_gb} GB / {threshold} GB limit",
                )
            alerts.append(alert)

        return alerts

    def evaluate_error_rate(self, error_rate: dict):
        """Evaluate error rate from logs."""
        alerts = []
        rate = error_rate.get("error_rate_per_min", 0)
        threshold = self.thresholds["error_rate_per_min"]
        alert_id = self._make_alert_id("error_rate", "high")

        is_breached = rate > threshold
        should_fire, fire_type = self.state_manager.should_fire(alert_id, is_active=is_breached)

        if should_fire:
            self.state_manager.record_fire(alert_id, fire_type)

            if fire_type == "cleared":
                alert = Alert(
                    alert_id=alert_id,
                    component="error_rate",
                    severity=AlertSeverity.INFO,
                    title="Error rate recovered",
                    message=f"Error rate is now {rate}/min (threshold: {threshold}/min).",
                    action="No action needed — auto-cleared.",
                )
            else:
                severity = AlertSeverity.CRITICAL if fire_type == "escalation" else AlertSeverity.WARNING
                title_suffix = "still high" if fire_type == "escalation" else "exceeds threshold"
                sample_errors = error_rate.get("sample_errors", [])
                alert = Alert(
                    alert_id=alert_id,
                    component="error_rate",
                    severity=severity,
                    title=f"Error rate {title_suffix}",
                    message=f"Error rate is {rate}/min (threshold: {threshold}/min). {error_rate.get('error_count', 0)} errors in last {error_rate.get('window_minutes', 5)} min.",
                    action="Check log files in ~/.oyster/ for root cause.",
                    last_error=sample_errors[0] if sample_errors else None,
                    recent_samples=f"{error_rate.get('error_count', 0)} errors in {error_rate.get('window_minutes', 5)} min",
                )
            alerts.append(alert)

        return alerts

    def evaluate_disk(self, disk: dict):
        """Evaluate disk space."""
        alerts = []
        free_gb = disk.get("free_disk_gb")
        threshold = self.thresholds["disk_free_min_gb"]
        alert_id = self._make_alert_id("disk", "low_space")

        if free_gb is None:
            # Can't determine — don't alert
            return alerts

        is_breached = free_gb < threshold
        should_fire, fire_type = self.state_manager.should_fire(alert_id, is_active=is_breached)

        if should_fire:
            self.state_manager.record_fire(alert_id, fire_type)

            if fire_type == "cleared":
                alert = Alert(
                    alert_id=alert_id,
                    component="disk",
                    severity=AlertSeverity.INFO,
                    title="Disk space recovered",
                    message=f"Free disk on {disk.get('host', 'minipc1')} is now {free_gb} GB (threshold: {threshold} GB).",
                    action="No action needed — auto-cleared.",
                )
            else:
                severity = AlertSeverity.CRITICAL if fire_type == "escalation" else AlertSeverity.WARNING
                title_suffix = "critically low" if fire_type == "escalation" else "low"
                alert = Alert(
                    alert_id=alert_id,
                    component="disk",
                    severity=severity,
                    title=f"Disk space {title_suffix} on {disk.get('host', 'minipc1')}",
                    message=f"Free disk on {disk.get('host', 'minipc1')} is {free_gb} GB (threshold: {threshold} GB).",
                    action="Clean up old logs, recordings, or expand storage.",
                    recent_samples=f"{free_gb} GB free / {threshold} GB minimum",
                )
            alerts.append(alert)

        return alerts

    def evaluate_daemon_stuck(self, daemon_stuck: dict):
        """Evaluate if daemon is stuck."""
        alerts = []
        is_stuck = daemon_stuck.get("is_stuck", False)
        threshold = self.thresholds["daemon_stuck_minutes"]
        alert_id = self._make_alert_id("daemon", "stuck")

        should_fire, fire_type = self.state_manager.should_fire(alert_id, is_active=is_stuck)

        if should_fire:
            self.state_manager.record_fire(alert_id, fire_type)

            if fire_type == "cleared":
                alert = Alert(
                    alert_id=alert_id,
                    component="daemon",
                    severity=AlertSeverity.INFO,
                    title="Daemon recovered from stuck state",
                    message=f"Daemon state changed from {daemon_stuck.get('daemon_state', 'unknown')}.",
                    action="No action needed — auto-cleared.",
                )
            else:
                severity = AlertSeverity.CRITICAL if fire_type == "escalation" else AlertSeverity.WARNING
                title_suffix = "still stuck" if fire_type == "escalation" else "stuck"
                alert = Alert(
                    alert_id=alert_id,
                    component="daemon",
                    severity=severity,
                    title=f"Recorder daemon {title_suffix}",
                    message=f"Daemon has been in state '{daemon_stuck.get('daemon_state', 'unknown')}' for {daemon_stuck.get('state_age_minutes', 0)} min (threshold: {threshold} min). Game state growing: {daemon_stuck.get('game_state_growing', 'unknown')}.",
                    action="Restart recorder daemon or check for disk I/O issues.",
                    recent_samples=f"State: {daemon_stuck.get('daemon_state', 'unknown')}, Age: {daemon_stuck.get('state_age_minutes', 0)} min",
                )
            alerts.append(alert)

        return alerts

    def process_metrics(self, metrics: dict) -> list[Alert]:
        """Process a full metrics dict and return all alerts to fire."""
        all_alerts = []

        # Evaluate each metric category
        health = metrics.get("health", {})
        all_alerts.extend(self.evaluate_health(health))

        backlog = metrics.get("upload_backlog", {})
        all_alerts.extend(self.evaluate_upload_backlog(backlog))

        error_rate = metrics.get("error_rate", {})
        all_alerts.extend(self.evaluate_error_rate(error_rate))

        disk = metrics.get("disk", {})
        all_alerts.extend(self.evaluate_disk(disk))

        daemon_stuck = metrics.get("daemon_stuck", {})
        all_alerts.extend(self.evaluate_daemon_stuck(daemon_stuck))

        # Send all alerts
        for alert in all_alerts:
            log.info(f"Firing alert [{alert.fire_type if hasattr(alert, 'fire_type') else 'unknown'}]: {alert.title}")
            self.webhook_sender.send_all(alert)

        # Try to flush offline queue
        self.webhook_sender.flush_queue()

        return all_alerts


def main():
    """Main entry point — reads metrics from stdin or file and dispatches alerts."""
    config = load_config()
    dispatcher = AlertDispatcher(config)

    # If metrics file provided as argument, process it
    if len(sys.argv) > 1:
        metrics_file = sys.argv[1]
        if os.path.exists(metrics_file):
            with open(metrics_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            metrics = json.loads(line)
                            alerts = dispatcher.process_metrics(metrics)
                            log.info(f"Processed metrics, {len(alerts)} alert(s) fired")
                        except json.JSONDecodeError as e:
                            log.debug("Failed to parse metrics line: %s", e)
        else:
            log.error(f"Metrics file not found: {metrics_file}")
            sys.exit(1)
    else:
        # Read from stdin
        for line in sys.stdin:
            line = line.strip()
            if line:
                try:
                    metrics = json.loads(line)
                    alerts = dispatcher.process_metrics(metrics)
                    log.info(f"Processed metrics, {len(alerts)} alert(s) fired")
                except json.JSONDecodeError as e:
                    log.debug("Failed to parse stdin metrics line: %s", e)


if __name__ == "__main__":
    main()
