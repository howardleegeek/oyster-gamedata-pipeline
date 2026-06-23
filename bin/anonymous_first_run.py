#!/usr/bin/env python3
"""
bin/anonymous_first_run.py

Anonymous mode: consumer can install + record + accumulate clips locally
without ANY signup or email; opt-in to account later via in-app prompt;
clips queued locally, uploaded only after opt-in.

Usage:
    python -m bin.anonymous_first_run init
    python -m bin.anonymous_first_run record --title "My Clip" --duration 30
    python -m bin.anonymous_first_run status
    python -m bin.anonymous_first_run opt-in --email user@example.com
    python -m bin.anonymous_first_run upload
    python -m bin.anonymous_first_run cleanup
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClipStatus(str, Enum):
    """Lifecycle states for a locally queued clip."""
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


@dataclass
class ClipMetadata:
    """Metadata for a single recorded clip."""
    clip_id: str
    title: str
    duration_seconds: float
    created_at: str
    file_path: str
    status: ClipStatus = ClipStatus.PENDING
    upload_attempts: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert this metadata to a dictionary for serialization.

        Returns:
            Dict[str, Any]: Dictionary representation with status as string value.
        """
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClipMetadata":
        """Create ClipMetadata from a dictionary.

        Args:
            data: Dictionary containing clip metadata fields.

        Returns:
            ClipMetadata: New instance with parsed status enum.
        """
        data = dict(data)
        data["status"] = ClipStatus(data["status"])
        return cls(**data)


@dataclass
class AnonymousConfig:
    """Persistent configuration for an anonymous session."""
    anonymous_id: str
    created_at: str
    storage_path: str
    opted_in: bool = False
    email: Optional[str] = None
    account_id: Optional[str] = None
    last_activity: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert this configuration to a dictionary for serialization.

        Returns:
            Dict[str, Any]: Dictionary representation of the config.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnonymousConfig":
        """Create AnonymousConfig from a dictionary.

        Args:
            data: Dictionary containing anonymous config fields including
                anonymous_id, created_at, storage_path, and optional fields
                opted_in, email, account_id, last_activity.

        Returns:
            AnonymousConfig: New instance constructed from the dictionary values.
        """
        return cls(**data)


class AnonymousStorage:
    """Manages local storage for anonymous clip accumulation and queue."""

    DIR_NAME = ".anonymous_clips"
    CONFIG_FILE = "config.json"
    QUEUE_FILE = "queue.json"
    CLIPS_SUBDIR = "clips"

    def __init__(self, base_path: Optional[Path] = None) -> None:
        """Initialize anonymous storage with optional base path.

        Args:
            base_path: Root directory for storage. Defaults to user home.
        """
        self._base = base_path or Path.home()
        self.root = self._base / self.DIR_NAME
        self.config_path = self.root / self.CONFIG_FILE
        self.queue_path = self.root / self.QUEUE_FILE
        self.clips_dir = self.root / self.CLIPS_SUBDIR

    def _ensure_dirs(self) -> None:
        """Ensure storage directories exist."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.clips_dir.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path) -> Any:
        """Read JSON file, return None if missing or invalid."""
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _write_json(self, path: Path, data: Any) -> None:
        """Write data to JSON file atomically."""
        self._ensure_dirs()
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def load_config(self) -> Optional[AnonymousConfig]:
        """Load config from disk, return None if not initialized."""
        data = self._read_json(self.config_path)
        if data is None:
            return None
        return AnonymousConfig.from_dict(data)

    def save_config(self, config: AnonymousConfig) -> None:
        """Persist config to disk."""
        self._write_json(self.config_path, config.to_dict())

    def initialize(self) -> AnonymousConfig:
        """Initialize a new anonymous session with a unique ID."""
        self._ensure_dirs()
        config = AnonymousConfig(
            anonymous_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            storage_path=str(self.root),
        )
        self.save_config(config)
        return config

    def load_queue(self) -> List[ClipMetadata]:
        """Load the clip queue from disk."""
        data = self._read_json(self.queue_path)
        if data is None:
            return []
        return [ClipMetadata.from_dict(item) for item in data]

    def save_queue(self, queue: List[ClipMetadata]) -> None:
        """Persist the clip queue to disk."""
        self._write_json(self.queue_path, [item.to_dict() for item in queue])

    def enqueue_clip(self, title: str, duration_seconds: float, file_path: str) -> ClipMetadata:
        """Add a new clip to the queue."""
        self._ensure_dirs()
        queue = self.load_queue()
        clip = ClipMetadata(
            clip_id=str(uuid.uuid4()),
            title=title,
            duration_seconds=duration_seconds,
            created_at=datetime.now(timezone.utc).isoformat(),
            file_path=file_path,
        )
        queue.append(clip)
        self.save_queue(queue)
        return clip

    def clip_path(self, clip_id: str) -> Path:
        """Return the path to a clip's data file."""
        return self.clips_dir / f"{clip_id}.json"


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------


def cmd_init(args: Any) -> int:
    """Initialize anonymous mode."""
    storage = AnonymousStorage()
    config = storage.load_config()
    if config:
        print(f"Already initialized: {config.anonymous_id}")
        return 0
    config = storage.initialize()
    print(f"Initialized anonymous session: {config.anonymous_id}")
    print(f"Storage path: {config.storage_path}")
    return 0


def cmd_record(args: Any) -> int:
    """Record a new clip (simulated)."""
    storage = AnonymousStorage()
    config = storage.load_config()
    if not config:
        print("Error: not initialized. Run 'init' first.", file=sys.stderr)
        return 1

    # Simulate recording - in real implementation this would capture gameplay
    clip_file = storage.clips_dir / f"clip_{uuid.uuid4().hex[:8]}.dat"
    clip_file.touch()  # Create empty placeholder

    clip = storage.enqueue_clip(
        title=args.title,
        duration_seconds=args.duration,
        file_path=str(clip_file),
    )
    print(f"Recorded clip: {clip.clip_id}")
    print(f"  Title: {clip.title}")
    print(f"  Duration: {clip.duration_seconds}s")
    return 0


def cmd_status(args: Any) -> int:
    """Show current status."""
    storage = AnonymousStorage()
    config = storage.load_config()
    if not config:
        print("Not initialized. Run 'init' first.")
        return 0

    print(f"Anonymous ID: {config.anonymous_id}")
    print(f"Created: {config.created_at}")
    print(f"Opted-in: {config.opted_in}")
    if config.opted_in:
        print(f"Email: {config.email}")
        print(f"Account ID: {config.account_id}")
    print(f"Storage: {config.storage_path}")

    queue = storage.load_queue()
    print(f"\nQueued clips: {len(queue)}")
    for clip in queue:
        status_icon = "✓" if clip.status == ClipStatus.UPLOADED else "○"
        print(f"  {status_icon} {clip.clip_id[:8]}: {clip.title} ({clip.duration_seconds}s) [{clip.status.value}]")

    return 0


def cmd_opt_in(args: Any) -> int:
    """Opt-in to create an account."""
    storage = AnonymousStorage()
    config = storage.load_config()
    if not config:
        print("Error: not initialized. Run 'init' first.", file=sys.stderr)
        return 1

    if config.opted_in:
        print(f"Already opted-in with email: {config.email}")
        return 0

    # Simulate account creation - in real implementation this would call API
    config.opted_in = True
    config.email = args.email
    config.account_id = f"ACC-{uuid.uuid4().hex[:8].upper()}"
    config.last_activity = datetime.now(timezone.utc).isoformat()
    storage.save_config(config)

    print(f"Opted-in successfully!")
    print(f"  Email: {config.email}")
    print(f"  Account ID: {config.account_id}")
    return 0


def cmd_upload(args: Any) -> int:
    """Upload queued clips (requires opt-in)."""
    storage = AnonymousStorage()
    config = storage.load_config()
    if not config:
        print("Error: not initialized. Run 'init' first.", file=sys.stderr)
        return 1

    if not config.opted_in:
        print("Error: must opt-in before uploading. Run 'opt-in' first.", file=sys.stderr)
        return 1

    queue = storage.load_queue()
    pending = [c for c in queue if c.status == ClipStatus.PENDING]
    if not pending:
        print("No pending clips to upload.")
        return 0

    print(f"Uploading {len(pending)} clips...")
    for clip in pending:
        clip.status = ClipStatus.UPLOADING
        storage.save_queue(queue)

        # Simulate upload - in real implementation this would call API
        import time
        time.sleep(0.1)  # Simulate network latency

        clip.status = ClipStatus.UPLOADED
        clip.upload_attempts += 1
        storage.save_queue(queue)
        print(f"  Uploaded: {clip.clip_id[:8]}: {clip.title}")

    config.last_activity = datetime.now(timezone.utc).isoformat()
    storage.save_config(config)
    print("Upload complete.")
    return 0


def cmd_cleanup(args: Any) -> int:
    """Remove local storage (destructive)."""
    storage = AnonymousStorage()
    if not storage.root.exists():
        print("No local storage found.")
        return 0

    if not args.force:
        print("Warning: this will delete all local clips and configuration.")
        response = input("Continue? [y/N] ")
        if response.lower() != "y":
            print("Aborted.")
            return 1

    shutil.rmtree(storage.root)
    print(f"Removed: {storage.root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="anonymous_first_run",
        description="Anonymous mode clip recording and upload.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    subparsers.add_parser("init", help="Initialize anonymous session")

    # record
    record_parser = subparsers.add_parser("record", help="Record a new clip")
    record_parser.add_argument("--title", required=True, help="Clip title")
    record_parser.add_argument("--duration", type=float, default=30.0, help="Duration in seconds")

    # status
    subparsers.add_parser("status", help="Show current status")

    # opt-in
    optin_parser = subparsers.add_parser("opt-in", help="Opt-in to create account")
    optin_parser.add_argument("--email", required=True, help="Email address")

    # upload
    subparsers.add_parser("upload", help="Upload queued clips")

    # cleanup
    cleanup_parser = subparsers.add_parser("cleanup", help="Remove local storage")
    cleanup_parser.add_argument("--force", action="store_true", help="Skip confirmation")

    return parser


def main() -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "record": cmd_record,
        "status": cmd_status,
        "opt-in": cmd_opt_in,
        "upload": cmd_upload,
        "cleanup": cmd_cleanup,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())