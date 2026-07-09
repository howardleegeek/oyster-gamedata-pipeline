#!/usr/bin/env python3
"""
Defense Monotonic Clock - Blue team timing utilities.

Prefer time.monotonic_ns() inside capture loops for timing.
Use wall clock time only for timestamping events.
"""

import argparse
import sys
import time
from collections.abc import Callable


class DefenseClock:
    """Monotonic clock for timing, wall clock for timestamps."""

    @staticmethod
    def monotonic_ns() -> int:
        """Monotonic time for timing operations."""
        return time.monotonic_ns()

    @staticmethod
    def wallclock_ns() -> int:
        """Wall clock time for timestamps."""
        return time.time_ns()

    @staticmethod
    def wallclock_iso() -> str:
        """ISO format wall clock timestamp."""
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def capture_loop_monotonic(
    process_func: Callable[[int], None],
    interval_ms: float = 100.0,
    max_iterations: int | None = None,
) -> dict:
    """
    Capture loop using monotonic timing.

    Uses monotonic_ns() for interval control to avoid clock skew.
    Wall clock only used if process_func needs timestamps.
    """
    interval_ns = int(interval_ms * 1_000_000)
    durations = []
    iteration = 0

    while max_iterations is None or iteration < max_iterations:
        iteration += 1

        # Time with monotonic clock
        start_ns = DefenseClock.monotonic_ns()
        process_func(iteration)
        end_ns = DefenseClock.monotonic_ns()

        duration_ns = end_ns - start_ns
        durations.append(duration_ns)

        # Sleep to maintain interval using monotonic timing
        if duration_ns < interval_ns:
            sleep_ns = interval_ns - duration_ns
            if sleep_ns > 0:
                time.sleep(sleep_ns / 1_000_000_000.0)

    # Return stats
    if durations:
        return {
            "iterations": len(durations),
            "total_ns": sum(durations),
            "avg_ns": sum(durations) / len(durations),
            "min_ns": min(durations),
            "max_ns": max(durations),
        }
    return {}


def create_timestamped_event(event_type: str, data: dict) -> dict:
    """Create event with wall clock timestamp and monotonic reference."""
    return {
        "event_type": event_type,
        "timestamp_ns": DefenseClock.wallclock_ns(),  # Wall for stamp
        "timestamp_iso": DefenseClock.wallclock_iso(),
        "data": data,
        "monotonic_ref": DefenseClock.monotonic_ns(),  # Mono for reference
    }


def main(argv: list[str] | None = None) -> int:
    """CLI for defense monotonic clock."""
    parser = argparse.ArgumentParser(
        description="Use monotonic_ns() for timing, wall clock for stamps"
    )
    parser.add_argument("--demo", action="store_true", help="Run demo")

    args = parser.parse_args(argv)

    if args.demo:
        print("=== Defense Monotonic Clock ===")
        print("Monotonic for timing, wall clock for timestamps")
        print()

        print("1. Clock readings:")
        print(f"   Monotonic: {DefenseClock.monotonic_ns()} ns")
        print(f"   Wall clock: {DefenseClock.wallclock_ns()} ns")
        print(f"   Wall ISO: {DefenseClock.wallclock_iso()}")
        print()

        print("2. Timestamped event:")
        event = create_timestamped_event("demo", {"message": "Using monotonic for timing"})
        print(f"   Event: {event['event_type']}")
        print(f"   Time: {event['timestamp_iso']}")
        print(f"   Mono ref: {event['monotonic_ref']}")
        print()

        print("3. Capture loop with monotonic timing:")

        def process(i: int) -> None:
            """Example process function."""
            # Use wall clock only if timestamp needed
            if i % 2 == 0:
                ts = DefenseClock.wallclock_iso()
                print(f"    Iter {i} at {ts}")
            else:
                print(f"    Iter {i}")
            time.sleep(0.05)

        stats = capture_loop_monotonic(process, interval_ms=150.0, max_iterations=3)

        print()
        print("4. Timing statistics:")
        for key, value in stats.items():
            if key.endswith("_ns"):
                print(f"   {key}: {value / 1_000_000:.2f} ms")
            else:
                print(f"   {key}: {value}")
    else:
        parser.print_help()

    return 0


if __name__ == "__main__":
    sys.exit(main())
