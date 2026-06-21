#!/usr/bin/env python3
"""vendor_scenario_low_disk.py — Simulate vendor pre-flight with low disk space.

Walkthrough: 1 GB free disk — capture pre-flight check warns and aborts safely.
This script models a vendor deployment scenario where available disk space falls
below the safe threshold, triggering warnings and a graceful abort.

Usage:
    python3 bin/vendor_scenario_low_disk.py [--threshold-gb N] [--simulate-gb N]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from typing import Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD_GB: float = 5.0
DEFAULT_SIMULATE_GB: float = 1.0
MIN_REQUIRED_GB: float = 2.0
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(message)s"

logger = logging.getLogger("vendor_low_disk")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_free_disk_gb(path: str) -> float:
    """Return free disk space in gigabytes for the filesystem containing *path*."""
    usage = shutil.disk_usage(path)
    return usage.free / (1024 ** 3)


def check_disk_space(
    target_path: str,
    threshold_gb: float,
    simulate_gb: float | None = None,
) -> tuple[bool, list[str]]:
    """Run a pre-flight disk-space check.

    Parameters
    ----------
    target_path : str
        Directory whose filesystem will be inspected.
    threshold_gb : float
        Minimum free space (GB) required to proceed.
    simulate_gb : float | None
        If provided, pretend the free space equals this value (for testing).

    Returns
    -------
    (passed, warnings)
        *passed* is True when free space >= threshold.
        *warnings* collects human-readable messages.
    """
    warnings: list[str] = []

    if simulate_gb is not None:
        free_gb = simulate_gb
    else:
        free_gb = get_free_disk_gb(target_path)

    logger.info("Free disk space: %.2f GB (threshold: %.2f GB)", free_gb, threshold_gb)

    if free_gb < MIN_REQUIRED_GB:
        msg = (
            f"CRITICAL: Only {free_gb:.2f} GB free — below absolute minimum "
            f"of {MIN_REQUIRED_GB} GB.  Aborting."
        )
        warnings.append(msg)
        logger.error(msg)
        return False, warnings

    if free_gb < threshold_gb:
        msg = (
            f"WARNING: {free_gb:.2f} GB free is below the recommended "
            f"threshold of {threshold_gb} GB."
        )
        warnings.append(msg)
        logger.warning(msg)
        return False, warnings

    logger.info("Disk space check passed (%.2f GB >= %.2f GB).", free_gb, threshold_gb)
    return True, warnings


def create_temp_workspace() -> str:
    """Create a temporary workspace directory and return its path."""
    work_dir = tempfile.mkdtemp(prefix="vendor_low_disk_")
    logger.info("Created temporary workspace: %s", work_dir)
    return work_dir


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the low-disk vendor scenario walkthrough.

    Returns 0 on success, 1 when the pre-flight check aborts.
    """
    parser = argparse.ArgumentParser(
        description="Vendor pre-flight: low disk space scenario walkthrough.",
    )
    parser.add_argument(
        "--threshold-gb",
        type=float,
        default=DEFAULT_THRESHOLD_GB,
        help=f"Minimum free space required to proceed (default: {DEFAULT_THRESHOLD_GB} GB).",
    )
    parser.add_argument(
        "--simulate-gb",
        type=float,
        default=None,
        help="Simulate a specific amount of free disk space in GB (for testing).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format=LOG_FORMAT)

    work_dir = create_temp_workspace()
    try:
        passed, warnings = check_disk_space(
            target_path=work_dir,
            threshold_gb=args.threshold_gb,
            simulate_gb=args.simulate_gb,
        )

        if not passed:
            logger.error("Pre-flight check FAILED — %d warning(s) captured.", len(warnings))
            for idx, w in enumerate(warnings, start=1):
                logger.error("  [%d] %s", idx, w)
            logger.info("Aborting vendor deployment safely.")
            return 1

        logger.info("Pre-flight check passed. Proceeding with vendor deployment.")
        return 0

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.info("Cleaned up temporary workspace.")


if __name__ == "__main__":
    sys.exit(main())
