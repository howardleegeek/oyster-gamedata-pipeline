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
        """Convert clip metadata to a dictionary representation.

        Returns:
            dict: Dictionary with keys clip_id, file_path, size_bytes,
                last_accessed (ISO format), and status.
        """
        return {"clip_id": self.clip_id, "file_path": str(self.file_path),
                "size_bytes": self.size_bytes, "last_accessed": self.last_accessed.isoformat(),
                "status": self.status}

    @classmethod
    def from_dict(cls, data: dict) -> "ClipMetadata":
        """Create a ClipMetadata instance from a dictionary representation.

        Args:
            data: Dictionary with keys clip_id, file_path, size_bytes,
                last_accessed (ISO format string), and status.

        Returns:
            ClipMetadata: New instance initialized from the dictionary data.
        """
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
        """Get current usage as a fraction of capacity."""
        return self.get_current_usage() / self.cap_bytes

    def load_metadata(self) -> list[ClipMetadata]:
        """Load clip metadata from JSON file."""
        if not self.metadata_path.exists():
            return []
        with open(self.metadata_path) as f:
            data = json.load(f)
            return [ClipMetadata.from_dict(item) for item in data]

    def save_metadata(self, clips: list[ClipMetadata]) -> None:
        """Save clip metadata to JSON file."""
        with open(self.metadata_path, "w") as f:
            json.dump([c.to_dict() for c in clips], f, indent=2)

    def cleanup(self, dry_run: bool = False) -> int:
        """Delete uploaded clips to free space, oldest first.

        Args:
            dry_run: If True, only calculate freed space without deleting.

        Returns:
            Number of bytes freed.
        """
        clips = self.load_metadata()
        # Sort by last_accessed, oldest first
        clips.sort(key=lambda c: c.last_accessed)

        freed_bytes = 0
        for clip in clips:
            if clip.status != "uploaded":
                continue
            if self.get_current_usage() - freed_bytes <= self.cap_bytes:
                break
            if not dry_run:
                try:
                    clip.file_path.unlink()
                    freed_bytes += clip.size_bytes
                except OSError as e:
                    logger.error("Failed to delete %s: %s", clip.file_path, e)

        return freed_bytes


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for the disk space manager CLI.

    Parses command-line arguments and performs disk space management operations
    including checking usage, running cleanup, and dry-run mode.

    Args:
        argv: Command-line arguments (defaults to sys.argv if None).

    Returns:
        Exit code: 0 on success, 1 on error or when help is shown.
    """
    parser = argparse.ArgumentParser(description="Disk Space Manager")
    parser.add_argument("--check", action="store_true", help="Check disk usage")
    parser.add_argument("--cleanup", action="store_true", help="Run cleanup")
    parser.add_argument("--dry-run", action="store_true", help="Dry run cleanup")
    parser.add_argument("--cap", default="5GB", help="Storage cap (e.g. 10GB)")
    parser.add_argument("--clips-dir", default="clips", help="Clips directory")
    args = parser.parse_args(argv)

    # Parse cap
    cap_bytes = None
    if args.cap.endswith("GB"):
        cap_bytes = int(float(args.cap[:-2]) * BYTE_TO_GB)
    elif args.cap.endswith("MB"):
        cap_bytes = int(float(args.cap[:-2]) * 1024 * 1024)

    clips_dir = Path(args.clips_dir)
    if not clips_dir.exists():
        logger.error("Clips directory not found: %s", clips_dir)
        return 1

    manager = DiskSpaceManager(clips_dir, cap_bytes=cap_bytes)

    if args.check:
        pct = manager.get_usage_percentage() * 100
        logger.info("Disk usage: %.2f%%", pct)
        if pct >= WARNING_THRESHOLD * 100:
            logger.warning("Above warning threshold: %.2f%%", WARNING_THRESHOLD * 100)
        return 0

    if args.cleanup:
        freed = manager.cleanup(dry_run=args.dry_run)
        logger.info("Freed %d bytes", freed)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
