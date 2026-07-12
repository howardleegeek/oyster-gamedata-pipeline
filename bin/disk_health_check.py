#!/usr/bin/env python3
"""
Standalone CLI for disk health check.
"""

import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Import functions from recorder_rate_limiter
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from bin.recorder_rate_limiter import (
        SESSION_DIR,
        can_record_now,
        count_sessions_today,
        load_config,
        sum_pending_uploads_gb,
    )
except ImportError:
    # Fallback implementation if import fails
    SESSION_DIR = Path.home() / "Documents" / "OysterClips"
    CONFIG_DIR = Path.home() / ".oyster"
    CONFIG_FILE = CONFIG_DIR / "limits.json"
    DAILY_COUNTER_FILE = CONFIG_DIR / "daily_counter.json"

    def load_config():
        if not CONFIG_FILE.exists():
            return {
                "min_free_gb": 10.0,
                "max_daily_sessions": 50,
                "max_pending_gb": 100.0
            }
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {
                "min_free_gb": 10.0,
                "max_daily_sessions": 50,
                "max_pending_gb": 100.0
            }

    def count_sessions_today():
        today_utc = datetime.now(timezone.utc).date()
        count = 0
        try:
            for item in SESSION_DIR.iterdir():
                if item.is_dir() and item.name.startswith("clip-"):
                    try:
                        mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                        if mtime.date() == today_utc:
                            count += 1
                    except (OSError, AttributeError):
                        continue
        except (OSError, FileNotFoundError) as exc:
            logger.debug(
                "count_sessions_today: iterdir(%s) failed (non-fatal, returning 0): %s",
                SESSION_DIR,
                exc,
            )
        return count

    def sum_pending_uploads_gb():
        total_bytes = 0
        try:
            for item in SESSION_DIR.iterdir():
                if item.is_file() and item.name.endswith(".uploaded.tar.gz"):
                    try:
                        total_bytes += item.stat().st_size
                    except (OSError, AttributeError):
                        continue
        except (OSError, FileNotFoundError) as exc:
            logger.debug(
                "sum_pending_uploads_gb: iterdir(%s) failed (non-fatal, returning 0): %s",
                SESSION_DIR,
                exc,
            )
        return total_bytes / 1e9

    def can_record_now():
        config = load_config()

        try:
            free_gb = shutil.disk_usage(SESSION_DIR).free / 1e9
            if free_gb < config["min_free_gb"]:
                return False, f"disk free {free_gb:.1f}GB < {config['min_free_gb']}GB threshold"
        except (OSError, FileNotFoundError):
            return False, "cannot check disk space"

        today_count = count_sessions_today()
        if today_count >= config["max_daily_sessions"]:
            return False, f"daily quota {today_count}/{config['max_daily_sessions']} reached"

        pending_gb = sum_pending_uploads_gb()
        if pending_gb > config["max_pending_gb"]:
            return False, f"upload backlog {pending_gb:.1f}GB > {config['max_pending_gb']}GB"

        return True, "ok"


def main():
    """Main CLI function."""
    # Ensure session directory exists for disk usage check
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    # Load configuration
    config = load_config()

    # Get disk usage
    try:
        disk_usage = shutil.disk_usage(SESSION_DIR)
        free_gb = disk_usage.free / 1e9
        total_gb = disk_usage.total / 1e9
        used_gb = disk_usage.used / 1e9
        free_percent = (free_gb / total_gb) * 100 if total_gb > 0 else 0
    except (OSError, FileNotFoundError):
        print("Error: Cannot check disk space")
        sys.exit(1)

    # Get session count
    today_count = count_sessions_today()

    # Get pending uploads
    pending_gb = sum_pending_uploads_gb()

    # Check if recording is allowed
    allowed, reason = can_record_now()

    # Display results
    print("=== Oyster Disk Health Check ===")
    print()
    print("Disk space:")
    print(f"  Free: {free_gb:.1f} GB ({free_percent:.1f}%)")
    print(f"  Used: {used_gb:.1f} GB")
    print(f"  Total: {total_gb:.1f} GB")
    print(f"  Threshold: {config.get('min_free_gb', 10.0)} GB minimum free")
    print()
    print("Sessions today:")
    print(f"  Count: {today_count}")
    print(f"  Limit: {config.get('max_daily_sessions', 50)}")
    print(f"  Remaining: {config.get('max_daily_sessions', 50) - today_count}")
    print()
    print("Pending uploads:")
    print(f"  Size: {pending_gb:.1f} GB")
    print(f"  Limit: {config.get('max_pending_gb', 100.0)} GB")
    print()
    print(f"Status: {'HEALTHY' if allowed else 'BLOCKED'}")
    if not allowed:
        print(f"Reason: {reason}")
    print()

    # Show archive status if available
    archive_dir = SESSION_DIR / "archive"
    if archive_dir.exists():
        try:
            archive_size = sum(f.stat().st_size for f in archive_dir.rglob('*') if f.is_file())
            archive_gb = archive_size / 1e9
            archive_count = len(list(archive_dir.rglob('*.tar.gz*')))
            print("Archive:")
            print(f"  Size: {archive_gb:.1f} GB")
            print(f"  Files: {archive_count}")
        except (OSError, AttributeError) as exc:
            logger.debug(
                "disk_health_check: archive rglob/scan of %s failed (non-fatal): %s",
                archive_dir,
                exc,
            )

    # Return exit code based on health status
    sys.exit(0 if allowed else 1)


if __name__ == "__main__":
    # Create a symlink-friendly entry point
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        # JSON output format for programmatic use
        config = load_config()

        try:
            disk_usage = shutil.disk_usage(SESSION_DIR)
            free_gb = disk_usage.free / 1e9
        except (OSError, FileNotFoundError):
            free_gb = 0

        today_count = count_sessions_today()
        pending_gb = sum_pending_uploads_gb()
        allowed, reason = can_record_now()

        result = {
            "free_gb": round(free_gb, 2),
            "free_percent": round(
                (free_gb / (disk_usage.total / 1e9)) * 100, 1
            ) if disk_usage.total > 0 else 0,
            "sessions_today": today_count,
            "pending_gb": round(pending_gb, 2),
            "allowed": allowed,
            "reason": reason,
            "thresholds": {
                "min_free_gb": config.get("min_free_gb", 10.0),
                "max_daily_sessions": config.get("max_daily_sessions", 50),
                "max_pending_gb": config.get("max_pending_gb", 100.0)
            }
        }

        print(json.dumps(result, indent=2))
    else:
        main()
