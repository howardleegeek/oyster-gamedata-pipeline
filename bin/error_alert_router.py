#!/usr/bin/env python3
"""
G240 · bin/error_alert_router.py

Purpose:
    For critical-severity errors: rate-limited Slack / Discord webhook (configurable);
    dedup window 5 min per sha256_key; daily-summary digest for medium / low;
    replaces Sentry pager-style alerts.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class Severity(Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertRouter:
    """Routes error alerts with rate limiting, deduplication, and daily digests."""

    DEDUP_WINDOW = 300  # 5 minutes

    def __init__(self, config_path: Optional[str] = None):
        self.config = self._load_config(config_path)
        self.seen_errors: Dict[str, float] = {}
        self.daily_summary: Dict[str, List[Dict]] = defaultdict(list)
        self.rate_limit = self.config.get("rate_limit", 60)

    def _load_config(self, config_path: Optional[str]) -> Dict[str, Any]:
        config = {
            "slack_webhook": os.environ.get("SLACK_WEBHOOK_URL"),
            "discord_webhook": os.environ.get("DISCORD_WEBHOOK_URL"),
            "rate_limit": 60,
            "digest_file": os.environ.get("ERROR_DIGEST_FILE", "/var/log/error_digest.json"),
            "enabled": True,
        }
        if config_path and Path(config_path).exists():
            try:
                import yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    config.update(yaml.safe_load(f) or {})
            except Exception as e:
                logger.warning(f"Config load failed: {e}")
        return config

    def _error_key(self, error: Dict[str, Any]) -> str:
        key = "|".join(str(error.get(k, "")) for k in ["message", "type", "location", "stack_hash"])
        return hashlib.sha256(key.encode()).hexdigest()

    def _should_alert(self, key: str, severity: Severity) -> bool:
        now = time.time()
        if key in self.seen_errors:
            elapsed = now - self.seen_errors[key]
            window = self.rate_limit if severity == Severity.CRITICAL else self.DEDUP_WINDOW
            if elapsed < window:
                return False
        self.seen_errors[key] = now
        return True

    def _send_webhook(self, url: str, payload: Dict[str, Any]) -> bool:
        if not url:
            return False
        try:
            import urllib.request
            data = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except Exception as e:
            logger.error(f"Webhook failed: {e}")
            return False

    def _slack_payload(self, error: Dict, severity: Severity) -> Dict:
        colors = {Severity.LOW: "#36a64f", Severity.MEDIUM: "#ff9900", Severity.HIGH: "#ff6600", Severity.CRITICAL: "#ff0000"}
        return {"attachments": [{"color": colors.get(severity, "#808080"), "title": f"[{severity.value.upper()}] Error",
            "fields": [{"title": "Message", "value": error.get("message", "N/A"), "short": False},
                       {"title": "Type", "value": error.get("type", "N/A"), "short": True},
                       {"title": "Location", "value": error.get("location", "N/A"), "short": True}],
            "footer": "G240 Alert Router"}]}

    def _discord_payload(self, error: Dict, severity: Severity) -> Dict:
        colors = {Severity.LOW: 3447003, Severity.MEDIUM: 15105570, Severity.HIGH: 15158332, Severity.CRITICAL: 15158332}
        return {"embeds": [{"title": f"[{severity.value.upper()}] Error", "description": error.get("message", "N/A"),
            "color": colors.get(severity, 808080),
            "fields": [{"name": "Type", "value": error.get("type", "N/A"), "inline": True},
                       {"name": "Location", "value": error.get("location", "N/A"), "inline": True}],
            "timestamp": datetime.utcnow().isoformat()}]}

    def route_error(self, error: Dict[str, Any]) -> bool:
        """Route error based on severity. Returns True if alert was processed."""
        if not self.config.get("enabled", True):
            return False
        try:
            severity = Severity(error.get("severity", "medium").lower())
        except ValueError:
            severity = Severity.MEDIUM

        key = self._error_key(error)

        if severity in (Severity.CRITICAL, Severity.HIGH):
            if not self._should_alert(key, severity):
                return False
            sent = False
            if slack := self.config.get("slack_webhook"):
                sent = self._send_webhook(slack, self._slack_payload(error, severity)) or sent
            if discord := self.config.get("discord_webhook"):
                sent = self._send_webhook(discord, self._discord_payload(error, severity)) or sent
            if sent and severity == Severity.CRITICAL:
                logger.critical(f"Critical alert: {error.get('message', '')[:50]}")
            return sent
        else:
            self.daily_summary[severity.value].append({**error, "timestamp": datetime.now().isoformat()})
            logger.info(f"Added to digest: {severity.value}")
            return True

    def generate_digest(self) -> Dict[str, Any]:
        """Generate daily summary for medium/low errors."""
        digest = {"generated_at": datetime.now().isoformat(), "summaries": {}, "total_errors": 0}
        for sev in [Severity.LOW, Severity.MEDIUM]:
            if errors := self.daily_summary.get(sev.value, []):
                digest["summaries"][sev.value] = {"count": len(errors), "errors": errors[-100:]}
                digest["total_errors"] += len(errors)
        return digest

    def save_digest(self, path: Optional[str] = None) -> bool:
        """Save digest to file."""
        path = path or self.config.get("digest_file", "/var/log/error_digest.json")
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.generate_digest(), f, indent=2)
            logger.info(f"Digest saved: {path}")
            return True
        except Exception as e:
            logger.error(f"Digest save failed: {e}")
            return False

    def send_digest_webhook(self) -> bool:
        """Send digest via webhook."""
        digest = self.generate_digest()
        if digest["total_errors"] == 0:
            return True
        payload = {"text": f"Daily Error Summary: {digest['total_errors']} errors",
            "attachments": [{"color": "#ff9900", "title": "Daily Error Digest",
                "fields": [{"title": k, "value": f"{v['count']} errors", "short": True}
                          for k, v in digest["summaries"].items()]}]}
        return self._send_webhook(self.config.get("slack_webhook", ""), payload)


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Route error alerts with rate limiting and deduplication")
    parser.add_argument("--config", "-c", help="YAML config file path")
    parser.add_argument("--error", "-e", help="JSON-encoded error to route")
    parser.add_argument("--digest", "-d", action="store_true", help="Generate daily digest")
    parser.add_argument("--send-digest", action="store_true", help="Send digest via webhook")
    parser.add_argument("--digest-file", help="Digest file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    router = AlertRouter(config_path=args.config)

    if args.digest:
        return 0 if router.save_digest(args.digest_file) else 1
    if args.send_digest:
        return 0 if router.send_digest_webhook() else 1
    if args.error:
        try:
            error = json.loads(args.error)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            return 1
        router.route_error(error)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
