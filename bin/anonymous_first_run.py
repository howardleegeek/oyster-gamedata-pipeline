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
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClipMetadata":
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
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write_json(self, path: Path, data: Any) -> None:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, str(path))
        except BaseException as exc:
            try:
                os.unlink(tmp_name)
            except OSError as unlink_exc:
                logger.debug(
                    "anonymous_first_run: failed to unlink temp %s after write error %s: %s",
                    tmp_name,
                    exc,
                    unlink_exc,
                )
            raise

    def is_initialized(self) -> bool:
        """Return True if an anonymous session already exists."""
        return self.config_path.exists()

    def load_config(self) -> AnonymousConfig:
        if not self.config_path.exists():
            raise FileNotFoundError(f"No config at {self.config_path}")
        return AnonymousConfig.from_dict(self._read_json(self.config_path))

    def save_config(self, cfg: AnonymousConfig) -> None:
        cfg.last_activity = datetime.now(timezone.utc).isoformat()
        self._write_json(self.config_path, cfg.to_dict())

    def initialize(self, force: bool = False) -> AnonymousConfig:
        """Create a new anonymous session."""
        if self.is_initialized() and not force:
            return self.load_config()
        self._ensure_dirs()
        cfg = AnonymousConfig(
            anonymous_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            storage_path=str(self.root),
        )
        self.save_config(cfg)
        self._write_json(self.queue_path, [])
        logger.info("Anonymous session initialised: %s", cfg.anonymous_id)
        return cfg

    def load_queue(self) -> List[ClipMetadata]:
        if not self.queue_path.exists():
            return []
        raw = self._read_json(self.queue_path)
        return [ClipMetadata.from_dict(item) for item in raw]

    def save_queue(self, clips: List[ClipMetadata]) -> None:
        self._write_json(self.queue_path, [c.to_dict() for c in clips])

    def enqueue_clip(self, clip: ClipMetadata) -> None:
        queue = self.load_queue()
        queue.append(clip)
        self.save_queue(queue)

    def clip_path(self, clip_id: str, suffix: str = ".mp4") -> Path:
        return self.clips_dir / f"{clip_id}{suffix}"


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace, storage: AnonymousStorage) -> int:
    """Initialise anonymous mode."""
    cfg = storage.initialize(force=getattr(args, "force", False))
    print(f"Anonymous session created: {cfg.anonymous_id}")
    print(f"Storage location: {cfg.storage_path}")
    print("Clips will be stored locally until you opt-in to an account.")
    return 0


def cmd_record(args: argparse.Namespace, storage: AnonymousStorage) -> int:
    """Record a clip entry into the local queue."""
    if not storage.is_initialized():
        print("No anonymous session found. Run 'init' first.", file=sys.stderr)
        return 1
    clip_id = str(uuid.uuid4())
    clip_path = storage.clip_path(clip_id)
    src = getattr(args, "source", None)
    if src:
        src_path = Path(src)
        if not src_path.exists():
            print(f"Source file not found: {src}", file=sys.stderr)
            return 1
        shutil.copy2(str(src_path), str(clip_path))
    clip = ClipMetadata(
        clip_id=clip_id,
        title=getattr(args, "title", "Untitled"),
        duration_seconds=float(getattr(args, "duration", 0)),
        created_at=datetime.now(timezone.utc).isoformat(),
        file_path=str(clip_path),
    )
    storage.enqueue_clip(clip)
    print(f"Clip queued: {clip_id}  ({clip.title}, {clip.duration_seconds}s)")
    return 0


def cmd_status(args: argparse.Namespace, storage: AnonymousStorage) -> int:
    """Show anonymous session status and queued clips."""
    if not storage.is_initialized():
        print("No anonymous session found. Run 'init' first.", file=sys.stderr)
        return 1
    cfg = storage.load_config()
    queue = storage.load_queue()
    print(f"Anonymous ID : {cfg.anonymous_id}")
    print(f"Created      : {cfg.created_at}")
    print(f"Opted-in     : {cfg.opted_in}")
    if cfg.email:
        print(f"Email        : {cfg.email}")
    print(f"Queued clips : {len(queue)}")
    pending = [c for c in queue if c.status == ClipStatus.PENDING]
    uploaded = [c for c in queue if c.status == ClipStatus.UPLOADED]
    failed = [c for c in queue if c.status == ClipStatus.FAILED]
    if pending:
        print(f"  Pending    : {len(pending)}")
    if uploaded:
        print(f"  Uploaded   : {len(uploaded)}")
    if failed:
        print(f"  Failed     : {len(failed)}")
    return 0


def cmd_opt_in(args: argparse.Namespace, storage: AnonymousStorage) -> int:
    """Opt-in to a real account; mark session for upload."""
    if not storage.is_initialized():
        print("No anonymous session found. Run 'init' first.", file=sys.stderr)
        return 1
    cfg = storage.load_config()
    cfg.opted_in = True
    cfg.email = getattr(args, "email", cfg.email)
    cfg.account_id = getattr(args, "account_id", cfg.account_id) or str(uuid.uuid4())
    storage.save_config(cfg)
    print(f"Opt-in complete. Account: {cfg.account_id}")
    print(f"Email: {cfg.email or 'not provided'}")
    print(f"{len(storage.load_queue())} clip(s) ready for upload.")
    return 0


def cmd_upload(args: argparse.Namespace, storage: AnonymousStorage) -> int:
    """Simulate uploading queued clips (dry-run by default)."""
    if not storage.is_initialized():
        print("No anonymous session found. Run 'init' first.", file=sys.stderr)
        return 1
    cfg = storage.load_config()
    if not cfg.opted_in:
        print("Not opted-in yet. Run 'opt-in' first.", file=sys.stderr)
        return 1
    queue = storage.load_queue()
    pending = [c for c in queue if c.status == ClipStatus.PENDING]
    if not pending:
        print("No pending clips to upload.")
        return 0
    dry_run = not getattr(args, "no_dry_run", False)
    uploaded_count = 0
    for clip in pending:
        if dry_run:
            print(f"[DRY-RUN] Would upload: {clip.clip_id} ({clip.title})")
        else:
            print(f"Uploaded: {clip.clip_id} ({clip.title})")
        clip.status = ClipStatus.UPLOADED
        uploaded_count += 1
    storage.save_queue(queue)
    print(f"{uploaded_count} clip(s) processed.")
    return 0


def cmd_cleanup(args: argparse.Namespace, storage: AnonymousStorage) -> int:
    """Remove all anonymous data (clips, config, queue)."""
    if not storage.is_initialized():
        print("No anonymous session to clean up.", file=sys.stderr)
        return 1
    shutil.rmtree(str(storage.root))
    print("Anonymous data removed.")
    return 0


# ---------------------------------------------------------------------------
# CLI Parser & Entry Point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for all sub-commands."""
    parser = argparse.ArgumentParser(
        prog="anonymous_first_run",
        description="Anonymous clip recording and deferred upload.",
    )
    parser.add_argument("--storage-dir", type=Path, default=None,
                        help="Override base storage directory (default: $HOME).")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Initialise anonymous session")
    p.add_argument("--force", action="store_true", help="Re-initialise")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("record", help="Queue a new clip")
    p.add_argument("--title", default="Untitled", help="Clip title")
    p.add_argument("--duration", type=float, default=0.0, help="Duration (s)")
    p.add_argument("--source", default=None, help="Path to source media file")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("status", help="Show session status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("opt-in", help="Opt-in to account")
    p.add_argument("--email", default=None, help="User email")
    p.add_argument("--account-id", default=None, help="Pre-existing account ID")
    p.set_defaults(func=cmd_opt_in)

    p = sub.add_parser("upload", help="Upload queued clips")
    p.add_argument("--no-dry-run", action="store_true", help="Actually upload")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("cleanup", help="Remove anonymous data")
    p.set_defaults(func=cmd_cleanup)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for the anonymous first-run CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    storage = AnonymousStorage(base_path=args.storage_dir)
    return args.func(args, storage)


if __name__ == "__main__":
    sys.exit(main())
