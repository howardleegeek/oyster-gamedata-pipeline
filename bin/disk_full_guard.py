#!/usr/bin/env python3
"""
R044 · bin/disk_full_guard.py — disk space monitor for capture

Purpose:
QA audit BLOCKER: disk-full mid-capture is uncaught, produces silently
truncated tarballs. This independent guard runs alongside capture, kills
the parent if free space < threshold.
"""

import argparse
import logging
import os
import shutil
import signal
import sys
import time

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


def get_free_gb(path: str) -> float:
    """
    Get free disk space in GB for the given path.

    Args:
        path: Path to check disk space for

    Returns:
        Free space in GB as float
    """
    try:
        stat = shutil.disk_usage(path)
        free_gb = stat.free / (1024 ** 3)  # Convert bytes to GB
        return free_gb
    except Exception as e:
        logger.error(f"Failed to get disk usage for {path}: {e}")
        raise


def watch_loop(path: str, min_gb: float, parent_pid: int,
               check_interval: float = 5.0) -> int:
    """
    Poll free space. If below threshold, send SIGTERM to parent_pid + log alert.

    Args:
        path: Path to monitor disk space for
        min_gb: Minimum free space threshold in GB
        parent_pid: PID of parent process to terminate if disk space is low
        check_interval: How often to check disk space in seconds

    Returns:
        Exit code: 0 if terminated normally, 1 if killed parent due to low disk space
    """
    logger.info(f"Starting disk space guard for path: {path}")
    logger.info(f"Minimum free space: {min_gb} GB")
    logger.info(f"Parent PID: {parent_pid}")
    logger.info(f"Check interval: {check_interval} seconds")

    try:
        while True:
            try:
                free_gb = get_free_gb(path)
                logger.debug(f"Free space: {free_gb:.2f} GB (threshold: {min_gb} GB)")

                if free_gb < min_gb:
                    logger.error(
                        f"CRITICAL: Free space {free_gb:.2f} GB below threshold {min_gb} GB"
                    )
                    logger.error(f"Sending SIGTERM to parent process {parent_pid}")

                    try:
                        os.kill(parent_pid, signal.SIGTERM)
                        logger.error(f"Successfully sent SIGTERM to parent process {parent_pid}")
                        return 1
                    except ProcessLookupError:
                        logger.error(f"Parent process {parent_pid} not found")
                        return 0
                    except PermissionError:
                        logger.error(
                            f"Permission denied sending signal to parent process {parent_pid}"
                        )
                        return 1
                    except Exception as e:
                        logger.error(f"Failed to send signal to parent process {parent_pid}: {e}")
                        return 1
                else:
                    logger.debug(f"Free space {free_gb:.2f} GB is above threshold {min_gb} GB")

            except Exception as e:
                logger.error(f"Error checking disk space: {e}")

            time.sleep(check_interval)

    except KeyboardInterrupt:
        logger.info("Disk space guard interrupted")
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Monitor disk space and kill parent process if below threshold"
    )
    parser.add_argument(
        "--path", required=True, help="Path to monitor disk space for"
    )
    parser.add_argument(
        "--min-gb", type=float, required=True, help="Minimum free space threshold in GB"
    )
    parser.add_argument(
        "--parent-pid", type=int, required=True, help="PID of parent process to terminate"
    )
    parser.add_argument(
        "--check-interval",
        type=float,
        default=5.0,
        help="How often to check disk space in seconds (default: 5.0)"
    )

    args = parser.parse_args()

    return watch_loop(
        path=args.path,
        min_gb=args.min_gb,
        parent_pid=args.parent_pid,
        check_interval=args.check_interval
    )


if __name__ == "__main__":
    sys.exit(main())
