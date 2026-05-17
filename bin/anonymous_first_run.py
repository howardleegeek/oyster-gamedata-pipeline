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
        return cls(**data)


class AnonymousStorage:
    """Manages local storage for anonymous clip accumulation and queue."""

    DIR_NAME = ".anonymous_clips"
    CONFIG_FILE = "config.json"
    QUEUE_FILE = "queue.json"
    CLIPS_SUBDIR = "clips"

    def __init__(self, base_path: Optional[Path] = None) -> None:
        self._base = base_path or Path.home()
        self.root = self._base / self.DIR_NAME
        self.config_path = self.root / self.CONFIG_FILE
        self.queue_path = self.root / self.QUEUE_FILE
        self.clips_dir = self.root / self.CLIPS_SUBDIR

    def _ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.clips_dir.mkdir(exist_ok=True)

    def _read_json(self, path: Path) -> Any:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: Path, data: Any) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_config(self) -> Optional[AnonymousConfig]:
        """Load configuration from disk if it exists.

        Returns:
            AnonymousConfig if config file exists, None otherwise.
        """
        if not self.config_path.exists():
            return None
        data = self._read_json(self.config_path)
        return AnonymousConfig.from_dict(data)

    def save_config(self, config: AnonymousConfig) -> None:
        """Save configuration to disk.

        Args:
            config: Configuration to persist.
        """
        self._ensure_dirs()
        self._write_json(self.config_path, config.to_dict())

    def initialize(self) -> AnonymousConfig:
        """Initialize a new anonymous session.

        Returns:
            Newly created AnonymousConfig.
        """
        config = AnonymousConfig(
            anonymous_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            storage_path=str(self.root),
        )
        self.save_config(config)
        return config

    def load_queue(self) -> List[ClipMetadata]:
        """Load the clip queue from disk.

        Returns:
            List of ClipMetadata entries, empty list if no queue exists.
        """
        if not self.queue_path.exists():
            return []
        data = self._read_json(self.queue_path)
        return [ClipMetadata.from_dict(item) for item in data]

    def save_queue(self, queue: List[ClipMetadata]) -> None:
        """Save the clip queue to disk.

        Args:
            queue: List of ClipMetadata to persist.
        """
        self._ensure_dirs()
        self._write_json(self.queue_path, [c.to_dict() for c in queue])

    def enqueue_clip(self, title: str, duration_seconds: float, file_path: str) -> ClipMetadata:
        """Add a new clip to the upload queue.

        Args:
            title: User-provided title for the clip.
            duration_seconds: Length of the clip in seconds.
            file_path: Path to the clip file on disk.

        Returns:
            Newly created ClipMetadata.
        """
        clip = ClipMetadata(
            clip_id=str(uuid.uuid4()),
            title=title,
            duration_seconds=duration_seconds,
            created_at=datetime.now(timezone.utc).isoformat(),
            file_path=file_path,
        )
        queue = self.load_queue()
        queue.append(clip)
        self.save_queue(queue)
        return clip

    def clip_path(self, clip_id: str) -> Path:
        """Get the filesystem path for a clip by ID.

        Args:
            clip_id: Unique identifier for the clip.

        Returns:
            Path to the clip file.
        """
        return self.clips_dir / f"{clip_id}.mp4"


def cmd_init(args: Any) -> int:
    """Initialize anonymous mode storage.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    storage = AnonymousStorage(Path(args.path) if args.path else None)
    config = storage.initialize()
    print(f"Initialized anonymous session: {config.anonymous_id}")
    print(f"Storage location: {storage.root}")
    return 0


def cmd_record(args: Any) -> int:
    """Record a new clip to the local queue.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    storage = AnonymousStorage(Path(args.path) if args.path else None)
    config = storage.load_config()
    if not config:
        print("Error: Not initialized. Run 'init' first.", file=sys.stderr)
        return 1

    # In a real implementation, this would capture the clip.
    # For now, we just enqueue a placeholder.
    clip = storage.enqueue_clip(
        title=args.title,
        duration_seconds=args.duration,
        file_path=str(storage.root / "clips" / f"{uuid.uuid4()}.mp4"),
    )
    print(f"Queued clip: {clip.clip_id}")
    return 0


def cmd_status(args: Any) -> int:
    """Show status of anonymous mode.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    storage = AnonymousStorage(Path(args.path) if args.path else None)
    config = storage.load_config()
    if not config:
        print("Not initialized. Run 'init' first.")
        return 0

    print(f"Anonymous ID: {config.anonymous_id}")
    print(f"Created at: {config.created_at}")
    print(f"Opted in: {config.opted_in}")
    if config.email:
        print(f"Email: {config.email}")

    queue = storage.load_queue()
    print(f"Queued clips: {len(queue)}")
    pending = sum(1 for c in queue if c.status == ClipStatus.PENDING)
    uploading = sum(1 for c in queue if c.status == ClipStatus.UPLOADING)
    uploaded = sum(1 for c in queue if c.status == ClipStatus.UPLOADED)
    failed = sum(1 for c in queue if c.status == ClipStatus.FAILED)
    print(f"  Pending: {pending}")
    print(f"  Uploading: {uploading}")
    print(f"  Uploaded: {uploaded}")
    print(f"  Failed: {failed}")
    return 0


def cmd_opt_in(args: Any) -> int:
    """Opt in to an account, enabling upload.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    storage = AnonymousStorage(Path(args.path) if args.path else None)
    config = storage.load_config()
    if not config:
        print("Error: Not initialized. Run 'init' first.", file=sys.stderr)
        return 1

    config.opted_in = True
    config.email = args.email
    config.last_activity = datetime.now(timezone.utc).isoformat()
    storage.save_config(config)
    print(f"Opted in with email: {args.email}")
    return 0


def cmd_upload(args: Any) -> int:
    """Upload queued clips to the server.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    storage = AnonymousStorage(Path(args.path) if args.path else None)
    config = storage.load_config()
    if not config:
        print("Error: Not initialized. Run 'init' first.", file=sys.stderr)
        return 1

    if not config.opted_in:
        print("Error: Not opted in. Run 'opt-in' first.", file=sys.stderr)
        return 1

    queue = storage.load_queue()
    pending = [c for c in queue if c.status == ClipStatus.PENDING]
    if not pending:
        print("No clips to upload.")
        return 0

    # In a real implementation, this would upload to a server.
    print(f"Uploading {len(pending)} clips...")
    for clip in pending:
        clip.status = ClipStatus.UPLOADED
    storage.save_queue(queue)
    print("Upload complete.")
    return 0


def cmd_cleanup(args: Any) -> int:
    """Remove uploaded clips from local storage.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    storage = AnonymousStorage(Path(args.path) if args.path else None)
    config = storage.load_config()
    if not config:
        print("Error: Not initialized. Run 'init' first.", file=sys.stderr)
        return 1

    queue = storage.load_queue()
    uploaded = [c for c in queue if c.status == ClipStatus.UPLOADED]
    remaining = [c for c in queue if c.status != ClipStatus.UPLOADED]

    for clip in uploaded:
        path = storage.clip_path(clip.clip_id)
        if path.exists():
            path.unlink()

    storage.save_queue(remaining)
    print(f"Cleaned up {len(uploaded)} uploaded clips.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description="Anonymous mode: record clips locally, upload after opt-in."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize anonymous mode")
    p_init.add_argument(
        "--path",
        help="Base path for storage (default: home directory)",
    )
    p_init.set_defaults(func=cmd_init)

    p_record = sub.add_parser("record", help="Record a new clip")
    p_record.add_argument("--title", required=True, help="Clip title")
    p_record.add_argument("--duration", type=float, required=True, help="Duration in seconds")
    p_record.add_argument(
        "--path",
        help="Base path for storage (default: home directory)",
    )
    p_record.set_defaults(func=cmd_record)

    p_status = sub.add_parser("status", help="Show status")
    p_status.add_argument(
        "--path",
        help="Base path for storage (default: home directory)",
    )
    p_status.set_defaults(func=cmd_status)

    p_opt_in = sub.add_parser("opt-in", help="Opt in to account")
    p_opt_in.add_argument("--email", required=True, help="Email address")
    p_opt_in.add_argument(
        "--path",
        help="Base path for storage (default: home directory)",
    )
    p_opt_in.set_defaults(func=cmd_opt_in)

    p_upload = sub.add_parser("upload", help="Upload queued clips")
    p_upload.add_argument(
        "--path",
        help="Base path for storage (default: home directory)",
    )
    p_upload.set_defaults(func=cmd_upload)

    p_cleanup = sub.add_parser("cleanup", help="Remove uploaded clips")
    p_cleanup.add_argument(
        "--path",
        help="Base path for storage (default: home directory)",
    )
    p_cleanup.set_defaults(func=cmd_cleanup)

    return parser


def main() -> int:
    """Main entry point for the anonymous first-run CLI.

    Returns:
        Exit code from the executed command.
    """
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
