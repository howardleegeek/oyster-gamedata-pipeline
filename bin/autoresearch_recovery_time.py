#!/usr/bin/env python3
"""
G119 · Autoresearch Recovery Time Measurement

Measures mean time to first new clip after adapter crash recovery.
Simulates kill-9 crash, restarts adapter, and tracks recovery metrics.
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def find_adapter_pid(adapter_name: str = "adapter") -> Optional[int]:
    """Find PID of running adapter process by name."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", adapter_name], capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split()[0])
    except (OSError, ValueError) as e:
        logger.warning("Failed to find adapter PID: %s", e)
    return None


def kill_adapter(pid: int) -> bool:
    """Kill adapter process with SIGKILL (kill -9)."""
    try:
        os.kill(pid, signal.SIGKILL)
        logger.info("Sent SIGKILL to adapter PID %d", pid)
        time.sleep(0.5)
        return True
    except (ProcessLookupError, OSError) as e:
        logger.warning("Failed to kill process %d: %s", pid, e)
        return False


def start_adapter(adapter_cmd: List[str], workdir: str) -> Optional[subprocess.Popen]:
    """Start adapter process and return Popen object."""
    try:
        proc = subprocess.Popen(adapter_cmd, cwd=workdir, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        logger.info("Started adapter with PID %d", proc.pid)
        return proc
    except OSError as e:
        logger.error("Failed to start adapter: %s", e)
        return None


def wait_for_first_clip(
    clip_dir: Path, timeout: float = 60.0, poll_interval: float = 0.5
) -> Tuple[bool, float]:
    """Wait for first new clip file to appear after restart."""
    start_time = time.time()
    initial_files = set(clip_dir.glob("*")) if clip_dir.exists() else set()
    clip_extensions = {".mp4", ".avi", ".mov", ".mkv"}

    while time.time() - start_time < timeout:
        current_files = set(clip_dir.glob("*")) if clip_dir.exists() else set()
        new_clips = [
            f for f in (current_files - initial_files) if f.suffix.lower() in clip_extensions
        ]
        if new_clips:
            logger.info("Detected new clip: %s", new_clips[0].name)
            return True, time.time() - start_time
        time.sleep(poll_interval)
    return False, timeout


def run_single_trial(
    adapter_cmd: List[str], clip_dir: Path, workdir: str, adapter_name: str
) -> Optional[float]:
    """Run single crash-recovery trial and return recovery time."""
    pid = find_adapter_pid(adapter_name)
    if pid is not None and not kill_adapter(pid):
        return None
    proc = start_adapter(adapter_cmd, workdir)
    if proc is None:
        return None
    success, recovery_time = wait_for_first_clip(clip_dir)
    if success:
        logger.info("Recovery time: %.2f seconds", recovery_time)
        return recovery_time
    logger.error("Timeout waiting for first clip")
    proc.terminate()
    return None


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for recovery time measurement."""
    parser = argparse.ArgumentParser(
        description="Measure mean time to first clip after adapter crash recovery"
    )
    parser.add_argument(
        "--adapter-cmd", nargs="+", required=True, help="Command to start the adapter"
    )
    parser.add_argument(
        "--clip-dir", type=Path, required=True, help="Directory to monitor for new clips"
    )
    parser.add_argument("--adapter-name", default="adapter", help="Adapter process name pattern")
    parser.add_argument("--trials", type=int, default=5, help="Number of crash-recovery trials")
    parser.add_argument("--timeout", type=float, default=60.0, help="Timeout per trial in seconds")
    parser.add_argument("--output", type=Path, help="Output JSON file for results")
    args = parser.parse_args(argv)

    if not args.clip_dir.exists():
        logger.error("Clip directory does not exist: %s", args.clip_dir)
        return 1

    with tempfile.TemporaryDirectory() as workdir:
        recovery_times: List[float] = []
        for trial in range(1, args.trials + 1):
            logger.info("=== Trial %d/%d ===", trial, args.trials)
            result = run_single_trial(args.adapter_cmd, args.clip_dir, workdir, args.adapter_name)
            if result is not None:
                recovery_times.append(result)
            time.sleep(1.0)

        if not recovery_times:
            logger.error("No successful trials")
            return 1

        mean_rt = sum(recovery_times) / len(recovery_times)
        results = {
            "trials": args.trials,
            "successful_trials": len(recovery_times),
            "recovery_times": recovery_times,
            "mean_recovery_time": mean_rt,
            "min_recovery_time": min(recovery_times),
            "max_recovery_time": max(recovery_times),
        }
        logger.info(
            "=== Results ===\nMean: %.2fs, Min: %.2fs, Max: %.2fs",
            results["mean_recovery_time"],
            results["min_recovery_time"],
            results["max_recovery_time"],
        )

        if args.output:
            args.output.write_text(json.dumps(results, indent=2))
            logger.info("Results written to %s", args.output)
        return 0


if __name__ == "__main__":
    sys.exit(main())
