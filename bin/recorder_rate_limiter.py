#!/usr/bin/env python3
"""
Recorder rate limiter - prevents continuous-capture daemon from filling disk.
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# Default configuration
SESSION_DIR = Path.home() / "Documents" / "OysterClips"
CONFIG_DIR = Path.home() / ".oyster"
CONFIG_FILE = CONFIG_DIR / "limits.json"
DAILY_COUNTER_FILE = CONFIG_DIR / "daily_counter.json"

# Default thresholds
DEFAULT_THRESHOLDS = {
    "min_free_gb": 10.0,
    "max_daily_sessions": 50,
    "max_pending_gb": 100.0,
    "auto_delete_after_archive": False,
    "archive_days": 14,
    "delete_after_days": 30
}


def load_config() -> dict:
    """Load configuration from limits.json, create with defaults if missing."""
    if not CONFIG_FILE.exists():
        # Create config directory if it doesn't exist
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Save default config
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_THRESHOLDS, f, indent=2)
        return DEFAULT_THRESHOLDS.copy()
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        # Ensure all default keys exist
        for key, value in DEFAULT_THRESHOLDS.items():
            if key not in config:
                config[key] = value
        return config
    except (json.JSONDecodeError, IOError) as exc:
        logger.debug("Failed to read config %s: %s", CONFIG_FILE, exc)
        return DEFAULT_THRESHOLDS.copy()


def count_sessions_today() -> int:
    """
    Count sessions recorded today (UTC).
    Persists counter across restarts.
    """
    # Ensure session directory exists
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load daily counter state
    today_utc = datetime.now(timezone.utc).date()
    counter_state = {"date": str(today_utc), "count": 0}
    
    if DAILY_COUNTER_FILE.exists():
        try:
            with open(DAILY_COUNTER_FILE, 'r') as f:
                saved_state = json.load(f)
                saved_date = datetime.fromisoformat(saved_state["date"]).date()
                if saved_date == today_utc:
                    counter_state["count"] = saved_state["count"]
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.debug("Failed to load daily counter %s: %s", DAILY_COUNTER_FILE, exc)

    # Count actual sessions in directory (as backup/verification)
    session_count = 0
    try:
        for item in SESSION_DIR.iterdir():
            if item.is_dir() and item.name.startswith("clip-"):
                # Check creation/modification time
                try:
                    mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                    if mtime.date() == today_utc:
                        session_count += 1
                except (OSError, AttributeError) as exc:
                    logger.debug("Failed to stat session item %s: %s", item, exc)
                    continue
    except (OSError, FileNotFoundError) as exc:
        logger.debug("Failed to list session dir %s: %s", SESSION_DIR, exc)

    # Use the larger of persisted count or actual count
    final_count = max(counter_state["count"], session_count)

    # Save updated count
    counter_state["count"] = final_count
    try:
        with open(DAILY_COUNTER_FILE, 'w') as f:
            json.dump(counter_state, f, indent=2)
    except IOError as exc:
        logger.debug("Failed to persist daily counter %s: %s", DAILY_COUNTER_FILE, exc)

    return final_count


def increment_daily_counter() -> None:
    """Increment the daily session counter."""
    count = count_sessions_today()
    today_utc = datetime.now(timezone.utc).date()
    counter_state = {"date": str(today_utc), "count": count + 1}

    try:
        with open(DAILY_COUNTER_FILE, 'w') as f:
            json.dump(counter_state, f, indent=2)
    except IOError as exc:
        logger.debug("Failed to persist daily counter %s: %s", DAILY_COUNTER_FILE, exc)


def sum_pending_uploads_gb() -> float:
    """
    Calculate total size of pending uploads in GB.
    Looks for .uploaded.tar.gz files that haven't been archived.
    """
    total_bytes = 0
    
    try:
        # Check for pending upload files
        for item in SESSION_DIR.iterdir():
            if item.is_file() and item.name.endswith(".uploaded.tar.gz"):
                try:
                    total_bytes += item.stat().st_size
                except (OSError, AttributeError) as exc:
                    logger.debug("Failed to stat upload %s: %s", item, exc)
                    continue

        # Also check for session directories that might not be compressed yet
        for item in SESSION_DIR.iterdir():
            if item.is_dir() and item.name.startswith("clip-"):
                # Skip if already has uploaded marker
                uploaded_marker = item / ".uploaded"
                if uploaded_marker.exists():
                    try:
                        total_bytes += sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                    except (OSError, AttributeError) as exc:
                        logger.debug("Failed to walk session dir %s: %s", item, exc)
                        continue
    except (OSError, FileNotFoundError) as exc:
        logger.debug("Failed to enumerate session dir %s: %s", SESSION_DIR, exc)

    return total_bytes / 1e9  # Convert to GB


def can_record_now() -> Tuple[bool, str]:
    """
    Returns (allowed, reason). If not allowed, reason is human-readable.
    """
    # Load config
    config = load_config()

    # 1. Disk space check
    try:
        free_bytes = shutil.disk_usage(SESSION_DIR).free
        free_gb = free_bytes / 1e9
        if free_gb < config["min_free_gb"]:
            return False, f"disk free {free_gb:.1f}GB < {config['min_free_gb']}GB threshold"
    except (OSError, FileNotFoundError) as exc:
        logger.debug("disk_usage(%s) failed: %s", SESSION_DIR, exc)
        return False, "cannot check disk space"
    
    # 2. Daily session quota
    today_count = count_sessions_today()
    if today_count >= config["max_daily_sessions"]:
        return False, f"daily quota {today_count}/{config['max_daily_sessions']} reached"
    
    # 3. Pending upload backlog
    pending_gb = sum_pending_uploads_gb()
    if pending_gb > config["max_pending_gb"]:
        return False, f"upload backlog {pending_gb:.1f}GB > {config['max_pending_gb']}GB; pause until cleared"
    
    return True, "ok"


def reset_daily_counter() -> None:
    """Reset the daily counter (for testing or manual intervention)."""
    today_utc = datetime.now(timezone.utc).date()
    counter_state = {"date": str(today_utc), "count": 0}
    
    try:
        with open(DAILY_COUNTER_FILE, 'w') as f:
            json.dump(counter_state, f, indent=2)
    except IOError as exc:
        logger.debug("Failed to reset daily counter %s: %s", DAILY_COUNTER_FILE, exc)


if __name__ == "__main__":
    # CLI interface for testing
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        allowed, reason = can_record_now()
        print(f"Allowed: {allowed}")
        print(f"Reason: {reason}")
        sys.exit(0 if allowed else 1)
    elif len(sys.argv) > 1 and sys.argv[1] == "--status":
        config = load_config()
        free_gb = shutil.disk_usage(SESSION_DIR).free / 1e9
        today_count = count_sessions_today()
        pending_gb = sum_pending_uploads_gb()
        
        print(f"Free space: {free_gb:.1f} GB (threshold: {config['min_free_gb']} GB)")
        print(f"Sessions today: {today_count} / {config['max_daily_sessions']}")
        print(f"Pending uploads: {pending_gb:.1f} GB / {config['max_pending_gb']} GB")
        
        allowed, reason = can_record_now()
        status = "HEALTHY" if allowed else f"BLOCKED: {reason}"
        print(f"Status: {status}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--increment":
        increment_daily_counter()
        print("Daily counter incremented")
    elif len(sys.argv) > 1 and sys.argv[1] == "--reset":
        reset_daily_counter()
        print("Daily counter reset")
    else:
        print("Usage:")
        print("  --check     : Check if recording is allowed")
        print("  --status    : Show detailed status")
        print("  --increment : Increment daily counter")
        print("  --reset     : Reset daily counter")
