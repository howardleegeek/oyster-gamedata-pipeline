#!/usr/bin/env python3
"""
Cron job: archive sessions older than 14 days that have been confirmed uploaded.
"""

import json
import logging
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Default configuration
SESSION_DIR = Path.home() / "Documents" / "OysterClips"
ARCHIVE_DIR = SESSION_DIR / "archive"
CONFIG_DIR = Path.home() / ".oyster"
CONFIG_FILE = CONFIG_DIR / "limits.json"

# Default thresholds
DEFAULT_THRESHOLDS = {
    "archive_days": 14,
    "delete_after_days": 30,
    "auto_delete_after_archive": False,
    "compress_with_zstd": False
}


def load_config() -> dict:
    """Load configuration from limits.json."""
    if not CONFIG_FILE.exists():
        return DEFAULT_THRESHOLDS.copy()

    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        # Ensure all default keys exist
        for key, value in DEFAULT_THRESHOLDS.items():
            if key not in config:
                config[key] = value
        return config
    except (json.JSONDecodeError, IOError):
        return DEFAULT_THRESHOLDS.copy()


def get_old_uploaded_files(days_old: int) -> List[Path]:
    """
    Find .uploaded.tar.gz files older than specified days.
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=days_old)
    cutoff_timestamp = cutoff_time.timestamp()

    old_files = []

    try:
        for item in SESSION_DIR.iterdir():
            if item.is_file() and item.name.endswith(".uploaded.tar.gz"):
                try:
                    mtime = item.stat().st_mtime
                    if mtime < cutoff_timestamp:
                        old_files.append(item)
                except (OSError, AttributeError) as exc:
                    logger.debug("auto_archive_old_uploaded: stat failed for %s: %s", item, exc)
                    continue
    except (OSError, FileNotFoundError) as exc:
        logger.debug("Failed to list session directory %s: %s", SESSION_DIR, exc)

    return old_files


def compress_with_zstd(file_path: Path) -> Optional[Path]:
    """
    Compress file with zstd for better compression.
    Returns path to compressed file or None if failed.
    """
    compressed_path = file_path.with_suffix(file_path.suffix + ".zst")

    try:
        # Check if zstd is available
        result = subprocess.run(
            ["zstd", "--version"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return None

        # Compress the file
        result = subprocess.run(
            ["zstd", "-f", "-q", str(file_path), "-o", str(compressed_path)],
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and compressed_path.exists():
            # Remove original file after successful compression
            file_path.unlink()
            return compressed_path
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        logger.debug("zstd compression failed for %s: %s", file_path, exc)

    return None


def archive_old_files() -> dict:
    """
    Archive old uploaded files.
    Returns statistics about the operation.
    """
    config = load_config()
    archive_days = config.get("archive_days", 14)
    compress = config.get("compress_with_zstd", False)

    # Ensure archive directory exists
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    old_files = get_old_uploaded_files(archive_days)
    stats = {
        "total_found": len(old_files),
        "archived": 0,
        "compressed": 0,
        "failed": 0,
        "deleted": 0
    }

    for file_path in old_files:
        try:
            # Move to archive directory
            dest_path = ARCHIVE_DIR / file_path.name

            # If file already exists in archive, add timestamp
            if dest_path.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                new_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
                dest_path = ARCHIVE_DIR / new_name

            # Move the file
            shutil.move(str(file_path), str(dest_path))
            stats["archived"] += 1

            # Optionally compress with zstd
            if compress:
                compressed = compress_with_zstd(dest_path)
                if compressed:
                    stats["compressed"] += 1

        except (OSError, shutil.Error) as e:
            print(f"Failed to archive {file_path}: {e}")
            stats["failed"] += 1

    # Check for old archived files to delete
    if config.get("auto_delete_after_archive", False):
        delete_days = config.get("delete_after_days", 30)
        delete_cutoff = datetime.now(timezone.utc) - timedelta(days=delete_days)
        delete_cutoff_timestamp = delete_cutoff.timestamp()

        try:
            for item in ARCHIVE_DIR.iterdir():
                if item.is_file():
                    try:
                        mtime = item.stat().st_mtime
                        if mtime < delete_cutoff_timestamp:
                            item.unlink()
                            stats["deleted"] += 1
                    except (OSError, AttributeError) as exc:
                        logger.debug(
                            "auto_archive_old_uploaded: stat/unlink failed for %s: %s", item, exc
                        )
                        continue
        except (OSError, FileNotFoundError) as exc:
            logger.debug("Failed to iterate archive dir %s: %s", ARCHIVE_DIR, exc)

    return stats


def cleanup_old_session_dirs() -> dict:
    """
    Clean up old session directories that have been uploaded.
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=7)  # 7 days as default
    cutoff_timestamp = cutoff_time.timestamp()

    stats = {"directories_removed": 0, "total_space_freed_gb": 0.0}

    try:
        for item in SESSION_DIR.iterdir():
            if item.is_dir() and item.name.startswith("clip-"):
                # Check if directory has been uploaded
                uploaded_marker = item / ".uploaded"
                if uploaded_marker.exists():
                    try:
                        mtime = uploaded_marker.stat().st_mtime
                        if mtime < cutoff_timestamp:
                            # Calculate size before deletion
                            dir_size = 0
                            try:
                                dir_size = sum(
                                f.stat().st_size for f in item.rglob("*") if f.is_file()
                            )
                            except (OSError, AttributeError) as exc:
                                logger.debug("Failed to compute size of %s: %s", item, exc)

                            # Remove directory
                            shutil.rmtree(item)
                            stats["directories_removed"] += 1
                            stats["total_space_freed_gb"] += dir_size / 1e9
                    except (OSError, AttributeError) as exc:
                        logger.debug(
                            "auto_archive_old_uploaded: rmtree/size failed for %s: %s", item, exc
                        )
                        continue
    except (OSError, FileNotFoundError) as exc:
        logger.debug("Failed to iterate session dir %s for cleanup: %s", SESSION_DIR, exc)

    return stats


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--cleanup-dirs":
        # Clean up old session directories
        stats = cleanup_old_session_dirs()
        print(f"Cleaned up {stats['directories_removed']} old session directories")
        print(f"Freed {stats['total_space_freed_gb']:.2f} GB")
    else:
        # Archive old uploaded files
        stats = archive_old_files()

        print("Archive operation completed:")
        print(f"  Found {stats['total_found']} old uploaded files")
        print(f"  Archived {stats['archived']} files")
        if stats['compressed'] > 0:
            print(f"  Compressed {stats['compressed']} files with zstd")
        if stats['failed'] > 0:
            print(f"  Failed to archive {stats['failed']} files")
        if stats['deleted'] > 0:
            print(f"  Deleted {stats['deleted']} old archived files")

        # Also clean up old session directories
        dir_stats = cleanup_old_session_dirs()
        if dir_stats['directories_removed'] > 0:
            print(f"\nCleaned up {dir_stats['directories_removed']} old session directories")
            print(f"Freed {dir_stats['total_space_freed_gb']:.2f} GB")
