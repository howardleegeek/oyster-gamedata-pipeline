#!/usr/bin/env python3
"""
Notification module for E2E test results.

Provides pluggable backends:
- telegram: posts to @howard_dispatch bot
- slack: webhook
- log: writes to ~/Downloads/e2e_results/notifications.log
- pushnotification: uses system notification
"""

import datetime
import json
import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict

DEFAULT_ARCHIVE_DIR = os.path.expanduser("~/Downloads/e2e_results")


class NotifierBase(ABC):
    """Base class for notification backends."""
    
    @abstractmethod
    def send(self, session_id: str, report: Dict[str, Any]) -> bool:
        """Send notification. Returns True on success."""
        pass


class LogNotifier(NotifierBase):
    """Log notification to file."""
    
    def __init__(self, log_file: str = None):
        self.log_file = Path(log_file or DEFAULT_ARCHIVE_DIR) / "notifications.log"
    
    def send(self, session_id: str, report: Dict[str, Any]) -> bool:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        overall = report.get("overall", "UNKNOWN")
        features = report.get("features", {})
        passed = sum(1 for f in features.values() if f.get("status") == "PASS")
        total = len(features)
        
        with open(self.log_file, "a") as f:
            f.write(f"[{datetime.datetime.utcnow().isoformat()}Z] ")
            f.write(f"Session: {session_id}, Overall: {overall} ")
            f.write(f"({passed}/{total} features passed)\n")
        
        print(f"Logged notification to {self.log_file}")
        return True


class TelegramNotifier(NotifierBase):
    """Send notification via Telegram bot."""
    
    def __init__(self, bot_token: str = None, chat_id: str = None):
        # Bot token and chat ID should be configured via environment or config file
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    
    def send(self, session_id: str, report: Dict[str, Any]) -> bool:
        if not self.bot_token or not self.chat_id:
            print("Telegram credentials not configured, using LogNotifier")
            return LogNotifier().send(session_id, report)
        
        overall = report.get("overall", "UNKNOWN")
        score = report.get("canonical_pipeline", {}).get("score", "N/A")
        
        message = f"🎮 E2E Test {overall}\n"
        message += f"Session: {session_id}\n"
        message += f"Score: {score}\n"
        
        features = report.get("features", {})
        for name, result in features.items():
            status = result.get("status", "UNKNOWN")
            emoji = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⏭️"
            message += f"{emoji} {name}: {status}\n"
        
        # Send via Telegram API
        import urllib.request
        import urllib.parse
        
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        try:
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode(data).encode(),
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status == 200
        except Exception as e:
            print(f"Telegram notification failed: {e}")
            return False


class SlackNotifier(NotifierBase):
    """Send notification via Slack webhook."""
    
    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    
    def send(self, session_id: str, report: Dict[str, Any]) -> bool:
        if not self.webhook_url:
            print("Slack webhook not configured, using LogNotifier")
            return LogNotifier().send(session_id, report)
        
        overall = report.get("overall", "UNKNOWN")
        score = report.get("canonical_pipeline", {}).get("score", "N/A")
        
        color = "#36a64f" if overall == "PASS" else "#ff0000"
        
        # Build Slack payload
        features = report.get("features", {})
        feature_text = ""
        for name, result in features.items():
            status = result.get("status", "UNKNOWN")
            emoji = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⏭️"
            feature_text += f"{emoji} {name}: {status}\n"
        
        payload = {
            "attachments": [{
                "color": color,
                "title": f"E2E Test {overall}: {session_id}",
                "fields": [
                    {"title": "Score", "value": score, "short": True},
                    {"title": "Session", "value": session_id, "short": True},
                    {"title": "Features", "value": feature_text, "short": False}
                ],
                "footer": "E2E Orchestrator",
                "ts": int(datetime.datetime.utcnow().timestamp())
            }]
        }
        
        # Send via webhook
        import urllib.request
        import urllib.parse
        
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status == 200
        except Exception as e:
            print(f"Slack notification failed: {e}")
            return False


class PushNotificationNotifier(NotifierBase):
    """Send system push notification."""
    
    def send(self, session_id: str, report: Dict[str, Any]) -> bool:
        overall = report.get("overall", "UNKNOWN")
        message = f"E2E Test {overall}: Session {session_id}"
        
        try:
            # Try macOS notification
            result = subprocess.run(
                ["osascript", "-e", f'display notification "{message}" with title "E2E Test"'],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Push notification failed: {e}")
            return LogNotifier().send(session_id, report)


def get_notifier(backend: str) -> NotifierBase:
    """Get notifier instance by backend name."""
    notifiers = {
        "log": LogNotifier,
        "telegram": TelegramNotifier,
        "slack": SlackNotifier,
        "pushnotification": PushNotificationNotifier,
    }
    
    notifier_class = notifiers.get(backend, LogNotifier)
    return notifier_class()


def send_notification(backend: str, session_id: str, report: Dict[str, Any]) -> bool:
    """Convenience function to send notification."""
    notifier = get_notifier(backend)
    return notifier.send(session_id, report)


if __name__ == "__main__":
    # Test notification
    import sys
    
    test_report = {
        "overall": "PASS",
        "session_id": "test_session_123",
        "canonical_pipeline": {"status": "PASS", "score": "101/105"},
        "features": {
            "preflight": {"status": "PASS", "evidence": "all 10 checks present"},
            "watchdog": {"status": "PASS", "evidence": "grade=PASS"},
            "provenance": {"status": "PASS", "evidence": "merkle verified"},
            "zbuffer": {"status": "SKIP", "evidence": "mod patch not deployed"},
            "batch": {"status": "PASS", "evidence": "route assigned"},
            "skip_depth": {"status": "PASS", "evidence": "baseline 89"},
        }
    }
    
    backend = sys.argv[1] if len(sys.argv) > 1 else "log"
    result = send_notification(backend, "test_session_123", test_report)
    print(f"Notification sent: {result}")
