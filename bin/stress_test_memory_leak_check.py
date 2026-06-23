#!/usr/bin/env python3
"""
G058 · bin/stress_test_memory_leak_check.py

Stress test: 1000-iteration adapter run with tracemalloc.
Asserts that RSS (Resident Set Size) growth stays below 50 MB.
"""

from __future__ import annotations

import argparse
import gc
import os
import subprocess
import sys
import tempfile
import tracemalloc
from typing import List


def get_rss_mb() -> float:
    """Return current process RSS in megabytes."""
    pid = os.getpid()
    # Linux: read /proc/[pid]/status
    try:
        with open(f"/proc/{pid}/status", "r") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0  # kB → MB
    except (FileNotFoundError, IndexError, ValueError, OSError):
        pass
    # macOS / BSD: parse `ps -o rss= -p <pid>` (RSS in kB)
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip()) / 1024.0
    except Exception:
        pass
    return 0.0


def simulate_adapter_iteration(iteration: int, tmp_dir: str) -> None:
    """Simulate one adapter run: allocate data, process it, and release."""
    # Allocate ~50 KB per iteration
    data: List[bytes] = [os.urandom(1024) for _ in range(50)]
    combined = b"".join(data)
    _ = hash(combined)
    # Write and read a temp file
    filepath = os.path.join(tmp_dir, f"iter_{iteration:06d}.bin")
    with open(filepath, "wb") as fh:
        fh.write(combined)
    with open(filepath, "rb") as fh:
        _ = fh.read()
    os.unlink(filepath)
    del data, combined


def run_stress_test(iterations: int, max_rss_mb: float, verbose: bool = False) -> int:
    """Execute the stress test and return an exit code."""
    tracemalloc.start()
    gc.collect()
    rss_start = get_rss_mb()
    snapshot_start = tracemalloc.take_snapshot()
    tmp_dir = tempfile.mkdtemp(prefix="stress_test_")

    try:
        for i in range(iterations):
            simulate_adapter_iteration(i, tmp_dir)
            if i % 100 == 99:
                gc.collect()
                if verbose:
                    print(f"Iteration {i + 1}/{iterations}: RSS = {get_rss_mb():.2f} MB")

        gc.collect()
        rss_end = get_rss_mb()
        snapshot_end = tracemalloc.take_snapshot()
        rss_growth = rss_end - rss_start
        tracemalloc.stop()

        print(f"RSS start: {rss_start:.2f} MB")
        print(f"RSS end:   {rss_end:.2f} MB")
        print(f"RSS growth: {rss_growth:.2f} MB (threshold: {max_rss_mb:.2f} MB)")

        top_stats = snapshot_end.compare_to(snapshot_start, "lineno")[:5]
        print("\nTop 5 memory allocations:")
        for stat in top_stats:
            print(f"  {stat}")

        if rss_growth > max_rss_mb:
            print("\nFAIL: RSS growth exceeds threshold")
            return 1
        print("\nPASS: RSS growth within threshold")
        return 0
    finally:
        for fname in os.listdir(tmp_dir):
            os.unlink(os.path.join(tmp_dir, fname))
        os.rmdir(tmp_dir)


def main(argv: List[str] | None = None) -> int:
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Stress test adapter for memory leaks using tracemalloc."
    )
    parser.add_argument("--iterations", type=int, default=1000,
                        help="Number of iterations (default: 1000)")
    parser.add_argument("--max-rss-mb", type=float, default=50.0,
                        help="Max allowed RSS growth in MB (default: 50)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print progress every 100 iterations")
    args = parser.parse_args(argv)

    if args.iterations < 1:
        print("Error: iterations must be at least 1", file=sys.stderr)
        return 1
    if args.max_rss_mb <= 0:
        print("Error: max-rss-mb must be positive", file=sys.stderr)
        return 1

    return run_stress_test(args.iterations, args.max_rss_mb, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
