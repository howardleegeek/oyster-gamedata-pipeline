#!/usr/bin/env python3
"""
Disk Space Manager - Auto-cleanup old local clips.

Features:
- Configurable storage cap (default 5 GB)
- LRU deletion of uploaded clips first
- Never deletes pending-upload clips
- Warns at 80% capacity

Usage:
    python bin/disk_space_manager.py --check
    python bin/disk_space_manager.py --cleanup
    python bin/disk_space_manager.py --dry-run
    python bin/disk_space_manager.py --cap 10GB
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_CAP_GB = 5
WARNING_THRESHOLD = 0.80
BYTE_TO_GB = 1024**3


class ClipMetadata:
    """Represents metadata for a local clip."""

    def __init__(self, clip_id: str, file_path: Path, size_bytes: int,
                 last_accessed: datetime, status: str) -> None:
        self.clip_id = clip_id
        self.file_path = file_path
        self.size_bytes = size_bytes
        self.last_accessed = last_accessed
        self.status = status  # "uploaded", "pending", "local_only"

    def to_dict(self) -> dict:
        return {"clip_id": self.clip_id, "file_path": str(self.file_path),
                "size_bytes": self.size_bytes, "last_accessed": self.last_accessed.isoformat(),
                "status": self.status}

    @classmethod
    def from_dict(cls, data: dict) -> "ClipMetadata":
        return cls(data["clip_id"], Path(data["file_path"]), data["size_bytes"],
                   datetime.fromisoformat(data["last_accessed"]), data["status"])


class DiskSpaceManager:
    """Manages disk space for local clips with LRU cleanup."""

    def __init__(self, clips_dir: Path, metadata_path: Optional[Path] = None,
                 cap_bytes: Optional[int] = None) -> None:
        self.clips_dir = clips_dir
        self.metadata_path = metadata_path or clips_dir / ".clip_metadata.json"
        self.cap_bytes = cap_bytes or DEFAULT_CAP_GB * BYTE_TO_GB
        if not self.clips_dir.exists():
            raise FileNotFoundError(f"Clips directory not found: {clips_dir}")

    def get_current_usage(self) -> int:
        """Calculate current disk usage in bytes."""
        total = 0
        if self.clips_dir.exists():
            for item in self.clips_dir.iterdir():
                if item.is_file() and not item.name.startswith("."):
                    total += item.stat().st_size
        return total

    def get_usage_percentage(self) -> float:
        return self.get_current_usage() / self.cap_bytes

    def load_metadata(self) -> dict:
        """Load clip metadata from JSON file."""
        metadata = {}
        if self.metadata_path.exists():
            try:
                with open(self.metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for clip_id, clip_data in data.items():
                        metadata[clip_id] = ClipMetadata.from_dict(clip_data)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load metadata: {e}")
        return metadata

    def save_metadata(self, metadata: dict) -> None:
        """Save clip metadata to JSON file."""
        data = {cid: clip.to_dict() for cid, clip in metadata.items()}
        with open(self.metadata_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_clips_sorted_by_lru(self) -> list[ClipMetadata]:
        """Get all clips sorted by last access time (LRU first)."""
        metadata = self.load_metadata()
        clips = []
        for item in self.clips_dir.iterdir():
            if item.is_file() and not item.name.startswith("."):
                clip_id = item.stem
                stat = item.stat()
                clip = metadata.get(clip_id) or ClipMetadata(
                    clip_id, item, stat.st_size,
                    datetime.fromtimestamp(stat.st_atime), "local_only")
                clips.append(clip)
        clips.sort(key=lambda c: c.last_accessed)
        return clips

    def get_deletable_clips(self) -> list[ClipMetadata]:
        """Get clips that can be safely deleted (not pending)."""
        return [c for c in self.get_clips_sorted_by_lru() if c.status != "pending"]

    def check_and_warn(self) -> bool:
        """Check storage usage and warn if above threshold. Returns True if warning issued."""
        usage_pct = self.get_usage_percentage()
        current_gb = self.get_current_usage() / BYTE_TO_GB
        cap_gb = self.cap_bytes / BYTE_TO_GB
        if usage_pct >= WARNING_THRESHOLD:
            logger.warning(f"Storage at {usage_pct*100:.1f}% ({current_gb:.2f} / {cap_gb:.2f} GB)")
            return True
        logger.info(f"Storage: {usage_pct*100:.1f}% ({current_gb:.2f} / {cap_gb:.2f} GB)")
        return False

    def cleanup(self, dry_run: bool = False, force: bool = False) -> tuple[int, int]:
        """Clean up old clips to stay under cap. Returns (files_deleted, bytes_freed)."""
        current = self.get_current_usage()
        if current <= self.cap_bytes and not force:
            logger.info("Within cap, no cleanup needed")
            return 0, 0
        target = self.cap_bytes * 0.5 if force else self.cap_bytes
        to_free = current - target
        logger.info(f"Need to free {to_free / BYTE_TO_GB:.2f} GB")
        files_deleted = bytes_freed = 0
        for clip in self.get_deletable_clips():
            if bytes_freed >= to_free:
                break
            if not clip.file_path.exists():
                continue
            if dry_run:
                logger.info(f"[DRY] Would delete {clip.file_path.name} ({clip.size_bytes/BYTE_TO_GB:.4f} GB)")
            else:
                try:
                    clip.file_path.unlink()
                    logger.info(f"Deleted {clip.file_path.name}")
                except OSError as e:
                    logger.error(f"Failed to delete {clip.file_path}: {e}")
                    continue
            files_deleted += 1
            bytes_freed += clip.size_bytes
        return files_deleted, bytes_freed

    def get_status_summary(self) -> dict:
        """Get summary of current disk space status."""
        clips = self.get_clips_sorted_by_lru()
        counts = {"uploaded": 0, "pending": 0, "local_only": 0}
        for c in clips:
            counts[c.status] = counts.get(c.status, 0) + 1
        return {"current_bytes": self.get_current_usage(), "cap_bytes": self.cap_bytes,
                "usage_percentage": self.get_usage_percentage(), "total_clips": len(clips),
                "status_counts": counts, "above_warning": self.get_usage_percentage() >= WARNING_THRESHOLD}


def parse_size(size_str: str) -> int:
    """Parse size string like '5GB', '1024MB' to bytes."""
    size_str = size_str.strip().upper()
    units = (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024), ("B", 1))
    for unit, mult in units:
        if size_str.endswith(unit):
            try:
                return int(float(size_str[:-len(unit)]) * mult)
            except ValueError as e:
                logger.debug("parse_size: float conversion failed for %r: %s",
                             size_str[:-len(unit)], e)
    try:
        return int(size_str)
    except ValueError as e:
        raise ValueError(f"Invalid size: {size_str}") from e


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Manage local clip storage with automatic cleanup")
    parser.add_argument("--clips-dir", type=Path, default=Path.cwd() / "clips",
                        help="Directory containing clips")
    parser.add_argument("--cap", type=str, default=f"{DEFAULT_CAP_GB}GB",
                        help=f"Storage cap (default: {DEFAULT_CAP_GB}GB)")
    parser.add_argument("--check", action="store_true", help="Check current storage usage")
    parser.add_argument("--cleanup", action="store_true", help="Run cleanup to free space")
    parser.add_argument("--dry-run", action="store_true", help="Simulate cleanup without deleting")
    parser.add_argument("--force", action="store_true", help="Force cleanup to 50%% of cap")
    parser.add_argument("--status", action="store_true", help="Show detailed status")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    try:
        cap_bytes = parse_size(args.cap)
    except ValueError as e:
        logger.error(f"Invalid --cap: {e}")
        return 1
    try:
        manager = DiskSpaceManager(args.clips_dir, cap_bytes=cap_bytes)
    except FileNotFoundError as e:
        logger.error(f"{e}")
        return 1
    if args.check:
        manager.check_and_warn()
        return 0
    if args.status:
        print(json.dumps(manager.get_status_summary(), indent=2, default=str))
        return 0
    if args.cleanup or args.dry_run:
        above_warning = manager.check_and_warn()
        deleted, freed = manager.cleanup(dry_run=args.dry_run, force=args.force)
        if args.dry_run:
            logger.info(f"[DRY] Would delete {deleted} files, free {freed/BYTE_TO_GB:.2f} GB")
        else:
            logger.info(f"Deleted {deleted} files, freed {freed/BYTE_TO_GB:.2f} GB")
        return 2 if above_warning else 0
    above_warning = manager.check_and_warn()
    return 2 if above_warning else 0


if __name__ == "__main__":
    sys.exit(main())
