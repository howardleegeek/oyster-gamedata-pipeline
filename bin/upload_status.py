#!/usr/bin/env python3
"""
CLI tool to query upload daemon state.
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

STATE_FILE = Path.home() / ".oyster" / "upload_state.json"


def format_size(bytes_size: int) -> str:
    """Format bytes to human-readable size."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f} PB"


def get_status() -> Dict[str, Any]:
    """Get current upload status."""
    if not STATE_FILE.exists():
        return {
            "pending": [],
            "uploading": [],
            "completed": [],
            "failed": [],
            "total_pending_size": 0,
            "total_completed_size": 0,
            "total_completed_count": 0,
            "last_24h": {"count": 0, "size": 0, "failures": 0},
        }

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    sessions = state.get("sessions", {})

    pending = []
    uploading = []
    completed = []
    failed = []

    total_pending_size = 0
    total_completed_size = 0

    for session_id, session in sessions.items():
        state_val = session.get("state", "pending")
        file_size = session.get("file_size", 0)

        if state_val == "pending":
            pending.append(session)
            total_pending_size += file_size
        elif state_val == "uploading":
            uploading.append(session)
        elif state_val == "completed":
            completed.append(session)
            total_completed_size += file_size
        elif state_val == "failed":
            failed.append(session)

    # Calculate last 24h stats
    last_24h_count = 0
    last_24h_size = 0
    last_24h_failures = 0

    cutoff = datetime.now() - timedelta(hours=24)

    for session in completed:
        completed_at = session.get("completed_at")
        if completed_at:
            try:
                dt = datetime.fromisoformat(completed_at)
                if dt >= cutoff:
                    last_24h_count += 1
                    last_24h_size += session.get("file_size", 0)
            except Exception:
                pass

    for session in failed:
        created_at = session.get("created_at")
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                if dt >= cutoff:
                    last_24h_failures += 1
            except Exception:
                pass

    return {
        "pending": pending,
        "uploading": uploading,
        "completed": completed,
        "failed": failed,
        "total_pending_size": total_pending_size,
        "total_completed_size": total_completed_size,
        "total_completed_count": len(completed),
        "last_24h": {"count": last_24h_count, "size": last_24h_size, "failures": last_24h_failures},
    }


def print_status():
    """Print formatted status."""
    status = get_status()

    # Pending sessions
    pending = status["pending"]
    uploading = status["uploading"]
    failed = status["failed"]
    last_24h = status["last_24h"]

    if not pending and not uploading and not failed:
        print("No uploads in progress")
        return

    # Pending
    if pending:
        pending_sizes = []
        for p in pending:
            size = p.get("file_size", 0)
            pending_sizes.append(format_size(size))

        if len(pending_sizes) == 1:
            print(f"Pending: 1 session ({pending_sizes[0]})")
        else:
            total = sum(p.get("file_size", 0) for p in pending)
            print(f"Pending: {len(pending)} sessions ({format_size(total)})")
    else:
        print("Pending: 0 sessions")

    # Uploading
    if uploading:
        for u in uploading:
            session_id = u.get("session_id", "unknown")
            progress = u.get("progress", 0)
            bandwidth = u.get("bandwidth_kbps", 0)
            print(f"Uploading: {session_id}.tar.gz ({progress:.0f}% / {format_size(bandwidth)}/s)")
    else:
        print("Uploading: nothing")

    # Failed
    if failed:
        print(f"Failed: {len(failed)} session(s)")
        for f in failed:
            session_id = f.get("session_id", "unknown")
            error = f.get("error", "unknown error")
            print(f"  - {session_id}: {error}")

    # Last 24h stats
    print(
        f"Last 24h: {last_24h['count']} sessions, {format_size(last_24h['size'])}, "
        f"{last_24h['failures']} failures"
    )


def print_json():
    """Print status as JSON."""
    status = get_status()
    print(json.dumps(status, indent=2))


def print_detailed():
    """Print detailed status for each session."""
    status = get_status()

    print("=== Pending ===")
    for p in status["pending"]:
        print(f"  {p['session_id']}: {format_size(p.get('file_size', 0))}")

    print("\n=== Uploading ===")
    for u in status["uploading"]:
        print(
            f"  {u['session_id']}: {u.get('progress', 0):.1f}% "
            f"({format_size(u.get('bandwidth_kbps', 0) * 1024)}/s)"
        )

    print("\n=== Completed ===")
    for c in status["completed"]:
        completed_at = c.get("completed_at", "unknown")
        print(f"  {c['session_id']}: {format_size(c.get('file_size', 0))} at {completed_at}")

    print("\n=== Failed ===")
    for f in status["failed"]:
        error = f.get("error", "unknown")
        print(f"  {f['session_id']}: {error}")


def main():
    parser = argparse.ArgumentParser(description="Query upload daemon status")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["status", "json", "detailed"],
        help="Command to run (default: status)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.json or args.command == "json":
        print_json()
    elif args.command == "detailed":
        print_detailed()
    else:
        print_status()


if __name__ == "__main__":
    main()
