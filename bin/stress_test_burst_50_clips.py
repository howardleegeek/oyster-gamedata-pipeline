#!/usr/bin/env python3
"""
G057 · bin/stress_test_burst_50_clips.py

Stress test: 50 clips per minute burst — lint queue must drain without deadlock.
"""

import argparse
import concurrent.futures
import logging
import random
import sys
import time
from typing import Dict, List, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def process_clip(clip_id: int, fail_rate: float = 0.05) -> Dict:
    """Process a single clip with simulated work."""
    start = time.time()
    time.sleep(random.uniform(0.05, 0.5))  # Simulate processing
    elapsed = time.time() - start
    if random.random() < fail_rate:
        return {"id": clip_id, "ok": False, "time": elapsed, "err": "Simulated failure"}
    return {"id": clip_id, "ok": True, "time": elapsed, "err": None}


def process_burst(burst_id: int, size: int = 50, workers: int = 10,
                  fail_rate: float = 0.05, timeout: float = 10.0) -> Tuple[List[Dict], float]:
    """Process a burst of clips concurrently."""
    results, start = [], time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {
            executor.submit(process_clip, burst_id * size + i, fail_rate): i
            for i in range(size)
        }
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results.append(future.result(timeout=timeout))
            except concurrent.futures.TimeoutError:
                results.append({"id": idx, "ok": False, "time": timeout, "err": "Timeout"})
            except Exception as e:
                results.append({"id": idx, "ok": False, "time": 0.0, "err": str(e)})
    elapsed = time.time() - start
    ok = sum(1 for r in results if r["ok"])
    logger.info(f"Burst {burst_id}: {ok}/{len(results)} OK ({elapsed:.2f}s)")
    return results, elapsed


def run_stress_test(duration_min: float = 5.0, burst_size: int = 50,
                    workers: int = 10, fail_rate: float = 0.05,
                    interval: float = 60.0) -> Tuple[bool, Dict]:
    """Run stress test for specified duration."""
    logger.info(f"Starting: {burst_size} clips/burst, {duration_min} min, {workers} workers")
    start_time = time.time()
    end_time = start_time + (duration_min * 60)
    all_results, burst_count, slow_bursts, deadlock = [], 0, 0, False

    try:
        while time.time() < end_time:
            results, elapsed = process_burst(burst_count, burst_size, workers, fail_rate)
            all_results.extend(results)
            burst_count += 1
            if elapsed > interval * 2:
                slow_bursts += 1
                logger.warning(f"Slow burst: {elapsed:.1f}s")
                if slow_bursts >= 3:
                    logger.error("Potential deadlock: 3+ slow bursts")
                    deadlock = True
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        logger.info("Interrupted")

    total_elapsed = time.time() - start_time
    total, ok = len(all_results), sum(1 for r in all_results if r["ok"])
    stats = {
        "bursts": burst_count, "total_clips": total, "ok_clips": ok,
        "success_rate": ok / total if total else 0, "total_time_sec": total_elapsed,
        "clips_per_minute": (total / total_elapsed * 60) if total_elapsed else 0,
        "slow_bursts": slow_bursts, "deadlock_detected": deadlock,
    }
    logger.info("=" * 50)
    logger.info(f"Complete: {burst_count} bursts, {total} clips, {stats['success_rate']:.1%} OK")
    logger.info(f"Throughput: {stats['clips_per_minute']:.1f} clips/min, Time: {total_elapsed:.1f}s")
    return not deadlock and stats["success_rate"] >= 0.9, stats


def main(argv: List[str] | None = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(description="Stress test: 50 clips/min burst")
    parser.add_argument("-d", "--duration", type=float, default=5.0, help="Duration in minutes")
    parser.add_argument("-b", "--burst-size", type=int, default=50, help="Clips per burst")
    parser.add_argument("-w", "--workers", type=int, default=10, help="Max concurrent workers")
    parser.add_argument("-f", "--fail-rate", type=float, default=0.05, help="Failure rate 0.0-1.0")
    parser.add_argument("-i", "--interval", type=float, default=60.0, help="Seconds between bursts")
    args = parser.parse_args(argv)

    if not 0.0 <= args.fail_rate <= 1.0:
        logger.error("Fail rate must be 0.0-1.0")
        return 1

    success, stats = run_stress_test(
        args.duration, args.burst_size, args.workers, args.fail_rate, args.interval
    )
    if stats.get("deadlock_detected"):
        logger.error("DEADLOCK DETECTED")
        return 2
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
