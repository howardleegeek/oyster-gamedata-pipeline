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
        """Return the current disk usage as a fraction of the configured capacity cap.

        Returns:
            float: Ratio of current usage bytes to cap bytes (e.g. 0.85 means 85% used).
        """
        return self.get_current_usage() / self.cap_bytes

    def load_metadata(self) -> dict:
        """Load clip metadata from JSON file."""
        metadata = {}
        if self.metadata_path.exists():
            try:
                with open(self.metadata_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Failed to load metadata: %s", e)
        return metadata

    def save_metadata(self, metadata: dict) -> None:
        """Save clip metadata to JSON file."""
        try:
            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
        except IOError as e:
            logger.error("Failed to save metadata: %s", e)

    def get_clips_by_status(self, status: str) -> list[ClipMetadata]:
        """Get list of clips filtered by status."""
        metadata = self.load_metadata()
        clips = []
        for clip_id, data in metadata.items():
            if data.get("status") == status:
                clips.append(ClipMetadata.from_dict({"clip_id": clip_id, **data}))
        return clips

    def cleanup(self, dry_run: bool = False) -> int:
        """Delete old uploaded clips to free space.

        Args:
            dry_run: If True, only report what would be deleted.

        Returns:
            int: Number of bytes freed (or would be freed if dry_run=True).
        """
        usage = self.get_current_usage()
        if usage <= self.cap_bytes:
            logger.info("Disk usage OK: %.2f%%", self.get_usage_percentage() * 100)
            return 0

        logger.warning("Disk usage exceeded: %.2f%%", self.get_usage_percentage() * 100)

        # Get uploaded clips sorted by last_accessed (LRU)
        uploaded_clips = self.get_clips_by_status("uploaded")
        uploaded_clips.sort(key=lambda c: c.last_accessed)

        freed_bytes = 0
        for clip in uploaded_clips:
            if self.get_current_usage() <= self.cap_bytes * WARNING_THRESHOLD:
                break
            if dry_run:
                logger.info("Would delete: %s (%d bytes)", clip.clip_id, clip.size_bytes)
            else:
                logger.info("Deleting: %s (%d bytes)", clip.clip_id, clip.size_bytes)
                try:
                    clip.file_path.unlink()
                    freed_bytes += clip.size_bytes
                except OSError as e:
                    logger.error("Failed to delete %s: %s", clip.file_path, e)

        return freed_bytes


def main(argv: Optional[list[str]] = None) -> int:
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
