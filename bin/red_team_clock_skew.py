#!/usr/bin/env python3
"""
bin/red_team_clock_skew.py

Red team utility: detect machine clock jumps backward (e.g., 1 hour)
and switch adapter to monotonic clock for reliable timing.
"""

import argparse
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class ClockSource(Enum):
    """Available clock sources."""
    SYSTEM = "system"
    MONOTONIC = "monotonic"


@dataclass
class ClockState:
    """Current state of the clock adapter."""
    source: ClockSource
    last_system_time: float
    last_monotonic_time: float
    skew_detected: bool
    skew_amount: float


class ClockAdapter:
    """Adapter that monitors system clock for backward jumps and switches to monotonic."""

    def __init__(self, threshold_seconds: float = 3600.0):
        self._threshold = threshold_seconds
        self._state = ClockState(
            source=ClockSource.SYSTEM,
            last_system_time=time.time(),
            last_monotonic_time=time.monotonic(),
            skew_detected=False, skew_amount=0.0
        )

    @property
    def current_source(self) -> ClockSource:
        return self._state.source

    @property
    def skew_detected(self) -> bool:
        return self._state.skew_detected

    @property
    def skew_amount(self) -> float:
        return self._state.skew_amount

    def get_time(self) -> float:
        """Get current time from active clock source."""
        return time.monotonic() if self._state.source == ClockSource.MONOTONIC else time.time()

    def check_for_skew(self) -> bool:
        """Check if system clock jumped backward beyond threshold. Returns True if switched."""
        current_system = time.time()
        current_monotonic = time.monotonic()
        skew = (current_system - self._state.last_system_time) - \
               (current_monotonic - self._state.last_monotonic_time)

        if skew < -self._threshold:
            self._state.skew_detected = True
            self._state.skew_amount = skew
            self._state.source = ClockSource.MONOTONIC
            return True

        self._state.last_system_time = current_system
        self._state.last_monotonic_time = current_monotonic
        return False

    def reset(self) -> None:
        """Reset adapter to initial state."""
        self._state = ClockState(
            source=ClockSource.SYSTEM,
            last_system_time=time.time(),
            last_monotonic_time=time.monotonic(),
            skew_detected=False, skew_amount=0.0
        )


def monitor_clock(adapter: ClockAdapter, duration_seconds: float,
                  callback: Optional[Callable[[ClockSource, float], None]] = None,
                  poll_interval: float = 0.1) -> bool:
    """Monitor clock for specified duration. Returns True if skew detected."""
    end_time = time.monotonic() + duration_seconds
    while time.monotonic() < end_time:
        if adapter.check_for_skew():
            if callback:
                callback(adapter.current_source, adapter.skew_amount)
            return True
        time.sleep(poll_interval)
    return False


def main(argv: Optional[list] = None) -> int:
    """Main entry point. Returns 0 if no skew, 1 if skew detected, 2 on error."""
    parser = argparse.ArgumentParser(
        description="Detect machine clock jumps backward and switch to monotonic clock.")
    parser.add_argument("--threshold", type=float, default=3600.0,
                        help="Backward jump threshold in seconds (default: 3600)")
    parser.add_argument("--monitor-duration", type=float, default=60.0,
                        help="Duration to monitor in seconds (default: 60)")
    parser.add_argument("--simulate-skew", type=float, default=None,
                        help="Simulate a clock skew of given seconds (for testing)")
    args = parser.parse_args(argv)

    try:
        adapter = ClockAdapter(threshold_seconds=args.threshold)

        def on_skew(source: ClockSource, skew: float) -> None:
            print(f"[ALERT] Clock skew detected: {skew:.2f} seconds")
            print(f"[ALERT] Switched to {source.value} clock")

        if args.simulate_skew is not None:
            adapter._state.last_system_time += args.simulate_skew

        print(f"Monitoring clock for {args.monitor_duration} seconds...")
        print(f"Threshold: {args.threshold} seconds backward")

        if monitor_clock(adapter, args.monitor_duration, on_skew):
            print(f"Result: Clock skew detected. Using {adapter.current_source.value} clock.")
            return 1
        print("Result: No clock skew detected.")
        return 0
    except KeyboardInterrupt:
        print("\nMonitoring interrupted.")
        return 2
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())