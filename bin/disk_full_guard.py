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
from typing import Optional

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
                    logger.error(f"CRITICAL: Free space {free_gb:.2f} GB below threshold {min_gb} GB")
                    logger.error(f"Sending SIGTERM to parent process {parent_pid}")
                    
                    try:
                        os.kill(parent_pid, signal.SIGTERM)
                        logger.error(f"Successfully sent SIGTERM to parent process {parent_pid}")
                        return 1
                    except ProcessLookupError:
                        logger.error(f"Parent process {parent_pid} not found")
                        return 0
                    except PermissionError:
                        logger.error(f"Permission denied sending signal to parent process {parent_pid}")
                        return 1
                    except Exception as e:
                        logger.error(f"Failed to send signal to parent process {parent_pid}: {e}")
                        return 1
                
                # Sleep for the check interval
                time.sleep(check_interval)
                
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down")
                return 0
            except Exception as e:
                logger.error(f"Error in watch loop: {e}")
                # Continue running despite errors
                time.sleep(check_interval)
                
    except Exception as e:
        logger.error(f"Fatal error in watch loop: {e}")
        return 1


def main(argv: Optional[list] = None) -> int:
    """
    CLI: --path /tmp --min-gb 5 --parent-pid 12345 [--check-interval 5]
    Designed to be exec'd by orchestrator: `bin/disk_full_guard.py --parent-pid $$ &`
    """
    parser = argparse.ArgumentParser(
        description="Disk space monitor that kills parent process when free space falls below threshold",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Monitor /tmp with 5GB threshold, parent PID 12345
  bin/disk_full_guard.py --path /tmp --min-gb 5 --parent-pid 12345
  
  # With custom check interval
  bin/disk_full_guard.py --path /tmp --min-gb 5 --parent-pid 12345 --check-interval 10
  
  # In a shell script ($$ gives current shell's PID)
  bin/disk_full_guard.py --path /tmp --min-gb 5 --parent-pid $$ &
        """
    )
    
    parser.add_argument(
        "--path",
        required=True,
        help="Path to monitor disk space for"
    )
    
    parser.add_argument(
        "--min-gb",
        type=float,
        required=True,
        help="Minimum free space threshold in GB"
    )
    
    parser.add_argument(
        "--parent-pid",
        type=int,
        required=True,
        help="PID of parent process to terminate if disk space is low"
    )
    
    parser.add_argument(
        "--check-interval",
        type=float,
        default=5.0,
        help="How often to check disk space in seconds (default: 5.0)"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args(argv)
    
    # Set debug logging if requested
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    # Validate arguments
    if args.min_gb <= 0:
        logger.error("--min-gb must be positive")
        return 1
    
    if args.check_interval <= 0:
        logger.error("--check-interval must be positive")
        return 1
    
    if args.parent_pid <= 0:
        logger.error("--parent-pid must be a valid PID")
        return 1
    
    # Check if path exists
    if not os.path.exists(args.path):
        logger.error(f"Path does not exist: {args.path}")
        return 1
    
    logger.info(f"Starting disk_full_guard with PID: {os.getpid()}")
    
    # Run the watch loop
    return watch_loop(
        path=args.path,
        min_gb=args.min_gb,
        parent_pid=args.parent_pid,
        check_interval=args.check_interval
    )


if __name__ == "__main__":
    sys.exit(main())
