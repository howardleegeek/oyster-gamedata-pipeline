#!/usr/bin/env python3
"""
bug_report.py — CLI tool for 内测 users to report bugs to a Discord channel.

Collects structured bug information interactively, optionally attaches
crash dumps and log excerpts, and posts everything to a Discord webhook.

Config:
  Webhook URL is read from ~/.oyster/config.json under the key
  "bug_report_webhook".  It is never hardcoded.

Constraints:
  - No game session data is uploaded.
  - No OAuth tokens / credentials are uploaded.
  - Max attachment size: 2 MB (Discord limit).
  - Retries once on transient HTTP errors (5xx / connection error).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import sys
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.expanduser("~/.oyster/config.json")
MAX_ATTACH_BYTES = 2 * 1024 * 1024  # 2 MB
DEFAULT_CRASH_DUMP_PATH = os.path.expanduser("~/.oyster/crash_dumps/latest.dmp")
DEFAULT_LOG_PATH = os.path.expanduser("~/.oyster/logs/OysterRecorder.log")
LOG_TAIL_LINES = 200
RETRY_COUNT = 1  # retry once on transient error

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_config() -> dict[str, Any]:
    """Load the oyster config file and return it as a dict."""
    path = Path(CONFIG_PATH)
    if not path.exists():
        print(
            f"Error: config file not found at {CONFIG_PATH}\n"
            "Please create ~/.oyster/config.json with a 'bug_report_webhook' key.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        print(f"Error: {CONFIG_PATH} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)


def get_webhook_url(config: dict[str, Any]) -> str:
    """Extract the Discord webhook URL from config, or exit with an error."""
    url = config.get("bug_report_webhook", "").strip()
    if not url:
        print(
            "Error: 'bug_report_webhook' is not set in ~/.oyster/config.json.\n"
            "Add your Discord webhook URL to proceed.",
            file=sys.stderr,
        )
        sys.exit(1)
    return url


def hash_user_identifier() -> str:
    """
    Return a SHA-256 hash of the current user's identifier for anonymous
    attribution.  Uses HOME + username as input so it is stable per machine
    but not reversible to PII.
    """
    raw = f"{os.environ.get('HOME', '')}:{os.environ.get('USER', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def prompt_severity() -> int:
    """Prompt for severity level (1-3)."""
    while True:
        val = input("Severity (1=low, 2=medium, 3=critical): ").strip()
        if val in ("1", "2", "3"):
            return int(val)
        print("  Please enter 1, 2, or 3.")


def prompt_required(label: str) -> str:
    """Prompt for a required non-empty string."""
    while True:
        val = input(f"{label}: ").strip()
        if val:
            return val
        print(f"  {label} cannot be empty.")


def prompt_yes_no(label: str) -> bool:
    """Prompt for a yes/no question."""
    while True:
        val = input(f"{label} (y/n): ").strip().lower()
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False
        print("  Please enter 'y' or 'n'.")


def read_crash_dump(path: str = DEFAULT_CRASH_DUMP_PATH) -> Optional[str]:
    """
    Read a crash dump file and return its base64-encoded content.
    Returns None if the file doesn't exist or exceeds MAX_ATTACH_BYTES.
    """
    p = Path(path)
    if not p.exists():
        print(f"  Crash dump not found at {p}, skipping.")
        return None
    if p.stat().st_size > MAX_ATTACH_BYTES:
        print(
            f"  Crash dump exceeds {MAX_ATTACH_BYTES / (1024*1024):.0f} MB limit "
            f"({p.stat().st_size / (1024*1024):.1f} MB), skipping."
        )
        return None
    with open(p, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def tail_log(
    path: str = DEFAULT_LOG_PATH, lines: int = LOG_TAIL_LINES
) -> Optional[str]:
    """
    Return the last *lines* of a log file as a string.
    Returns None if the file doesn't exist or exceeds MAX_ATTACH_BYTES.
    """
    p = Path(path)
    if not p.exists():
        print(f"  Log file not found at {p}, skipping.")
        return None
    if p.stat().st_size > MAX_ATTACH_BYTES:
        print(
            f"  Log file exceeds {MAX_ATTACH_BYTES / (1024*1024):.0f} MB limit, "
            "reading last lines only."
        )
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        result = "".join(tail)
        if len(result.encode("utf-8")) > MAX_ATTACH_BYTES:
            # Truncate to fit
            result = result.encode("utf-8")[:MAX_ATTACH_BYTES].decode(
                "utf-8", errors="ignore"
            )
        return result
    except Exception as exc:
        print(f"  Error reading log: {exc}", file=sys.stderr)
        return None


def build_discord_payload(
    report_id: str,
    severity: int,
    title: str,
    steps: str,
    expected: str,
    actual: str,
    user_hash: str,
    crash_dump_b64: Optional[str],
    log_tail: Optional[str],
) -> dict[str, Any]:
    """
    Build a Discord webhook embed payload.

    The payload contains:
      - An embed with structured bug fields.
      - Optional attachments (base64-encoded crash dump, log excerpt).
    """
    severity_labels = {1: "🟢 Low", 2: "🟡 Medium", 3: "🔴 Critical"}
    severity_label = severity_labels.get(severity, str(severity))

    fields = [
        {"name": "Severity", "value": severity_label, "inline": True},
        {"name": "Reporter (anon)", "value": f"`{user_hash}`", "inline": True},
        {
            "name": "Platform",
            "value": f"{platform.system()} {platform.release()}",
            "inline": True,
        },
        {
            "name": "Steps to Reproduce",
            "value": textwrap.shorten(steps, width=1024, placeholder="..."),
            "inline": False,
        },
        {
            "name": "Expected Behavior",
            "value": textwrap.shorten(expected, width=1024, placeholder="..."),
            "inline": False,
        },
        {
            "name": "Actual Behavior",
            "value": textwrap.shorten(actual, width=1024, placeholder="..."),
            "inline": False,
        },
    ]

    if crash_dump_b64:
        fields.append(
            {
                "name": "Crash Dump",
                "value": f"Attached (base64, {len(crash_dump_b64)} chars)",
                "inline": False,
            }
        )

    if log_tail:
        log_preview = textwrap.shorten(log_tail, width=200, placeholder="...")
        fields.append(
            {
                "name": "Log Excerpt",
                "value": f"```\n{log_preview}\n```",
                "inline": False,
            }
        )

    payload: dict[str, Any] = {
        "content": f"🐛 **Bug Report** `{report_id}`",
        "embeds": [
            {
                "title": title,
                "color": {1: 0x00FF00, 2: 0xFFFF00, 3: 0xFF0000}.get(
                    severity, 0x888888
                ),
                "fields": fields,
                "footer": {"text": f"Report ID: {report_id}"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }

    # Include attachments as extra fields (base64-encoded)
    attachments: dict[str, str] = {}
    if crash_dump_b64:
        attachments["crash_dump.b64"] = crash_dump_b64
    if log_tail:
        attachments["log_tail.txt"] = log_tail
    if attachments:
        payload["attachments_data"] = attachments

    return payload


def post_to_webhook(url: str, payload: dict[str, Any]) -> requests.Response:
    """
    POST the payload to the Discord webhook URL.
    Retries once on transient HTTP errors (5xx, connection errors).
    """
    headers = {"Content-Type": "application/json"}
    attempt = 0
    last_exc: Optional[Exception] = None

    while attempt <= RETRY_COUNT:
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code < 500:
                return resp
            # 5xx — retry
            print(f"  Server error {resp.status_code}, retrying...", file=sys.stderr)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            print(f"  Request error: {exc}, retrying...", file=sys.stderr)
        attempt += 1
        if attempt <= RETRY_COUNT:
            import time

            time.sleep(1)

    # All retries exhausted
    if last_exc:
        raise last_exc
    return resp  # type: ignore[name-defined]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Interactive bug-report flow."""
    print("=" * 60)
    print("  Oyster Bug Report Tool (内测)")
    print("=" * 60)
    print()

    # Load config & webhook
    config = load_config()
    webhook_url = get_webhook_url(config)

    # Collect info
    severity = prompt_severity()
    title = prompt_required("Title (1 line)")
    steps = prompt_required("Steps to reproduce")
    expected = prompt_required("Expected behavior")
    actual = prompt_required("Actual behavior")

    attach_crash = prompt_yes_no("Attach latest crash dump?")
    attach_log = prompt_yes_no("Attach last 200 lines of OysterRecorder.log?")

    # Read optional attachments
    crash_dump_b64: Optional[str] = None
    if attach_crash:
        crash_dump_b64 = read_crash_dump()

    log_tail: Optional[str] = None
    if attach_log:
        log_tail = tail_log()

    # Build report
    report_id = str(uuid.uuid4())
    user_hash = hash_user_identifier()

    payload = build_discord_payload(
        report_id=report_id,
        severity=severity,
        title=title,
        steps=steps,
        expected=expected,
        actual=actual,
        user_hash=user_hash,
        crash_dump_b64=crash_dump_b64,
        log_tail=log_tail,
    )

    # Send
    print()
    print("Sending report...")
    try:
        resp = post_to_webhook(webhook_url, payload)
        if resp.status_code in (200, 204):
            print(f"Report sent, ID: {report_id}")
        else:
            print(
                f"Failed to send report (HTTP {resp.status_code}): {resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)
    except requests.exceptions.RequestException as exc:
        print(f"Failed to send report: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
