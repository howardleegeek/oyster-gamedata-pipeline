#!/usr/bin/env python3
"""
Recovery Orchestrator: Scan staging dir for half-baked tarballs and resume/quarantine them.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Iterator

# Configuration
INCOMPLETE_SUFFIXES = (".part", ".tmp", ".partial", ".incomplete", ".downloading")
TAR_EXTENSIONS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar.zst")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def find_incomplete_files(directory: Path) -> Iterator[Path]:
    """Yield incomplete/corrupted tarball paths from staging directory."""
    if not directory.is_dir():
        logger.warning(f"Directory does not exist: {directory}")
        return

    for entry in directory.iterdir():
        if not entry.is_file():
            continue

        # Check for incomplete filename patterns
        if entry.name.endswith(INCOMPLETE_SUFFIXES):
            logger.info(f"Found incomplete (pattern): {entry.name}")
            yield entry
            continue

        # Check for corrupted tarballs
        if entry.name.endswith(TAR_EXTENSIONS) and is_corrupted(entry):
            logger.info(f"Found incomplete (corrupted): {entry.name}")
            yield entry


def is_corrupted(filepath: Path) -> bool:
    """Check if a tarball appears corrupted or incomplete."""
    try:
        if filepath.stat().st_size < 512:
            return True
        with tarfile.open(filepath, "r:*") as tar:
            tar.next()
        return False
    except Exception as e:
        logger.debug(
            "is_corrupted(%r) failed; treating as corrupted: %s",
            filepath, e, exc_info=True,
        )
        return True


def quarantine_file(filepath: Path, quarantine_dir: Path) -> Path:
    """Move file to quarantine with timestamp prefix."""
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = quarantine_dir / f"{timestamp}_{filepath.name}"
    shutil.move(str(filepath), str(destination))
    logger.info(f"Quarantined: {filepath.name} -> {destination.name}")
    return destination


def attempt_resume(filepath: Path) -> bool:
    """Attempt to resume processing of incomplete file."""
    logger.info(f"Attempting resume: {filepath.name}")
    if not filepath.exists():
        logger.warning(f"File no longer exists: {filepath}")
        return False
    # Placeholder for actual resume logic
    return True


def process_incomplete(
    staging_dir: Path,
    quarantine_dir: Path,
    resume: bool = True,
    dry_run: bool = False
) -> tuple[int, int]:
    """Process incomplete tarballs: resume or quarantine."""
    processed = 0
    quarantined = 0

    for incomplete_file in find_incomplete_files(staging_dir):
        if resume and attempt_resume(incomplete_file):
            processed += 1
            continue

        if not dry_run:
            quarantine_file(incomplete_file, quarantine_dir)
        else:
            logger.info(f"[DRY RUN] Would quarantine: {incomplete_file}")
        quarantined += 1

    return processed, quarantined


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Recovery Orchestrator: Scan staging dir for half-baked tarballs."
    )
    parser.add_argument("--staging", type=Path, required=True, help="Staging directory")
    parser.add_argument("--quarantine", type=Path, required=True, help="Quarantine directory")
    parser.add_argument("--no-resume", action="store_true", help="Skip resume, quarantine all")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without executing")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    if not args.staging.exists():
        logger.error(f"Staging directory does not exist: {args.staging}")
        return 1

    args.quarantine.mkdir(parents=True, exist_ok=True)

    logger.info("Recovery Orchestrator Starting")
    logger.info(f"Staging: {args.staging} | Quarantine: {args.quarantine}")
    logger.info(f"Resume: {not args.no_resume} | Dry-run: {args.dry_run}")

    try:
        processed, quarantined = process_incomplete(
            args.staging, args.quarantine,
            resume=not args.no_resume,
            dry_run=args.dry_run
        )
        logger.info(f"Complete: processed={processed}, quarantined={quarantined}")
        return 0
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
