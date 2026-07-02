#!/usr/bin/env python3
"""
Circuit breaker for S3 operations.

Production: trip after N consecutive S3 failures — halt uploads and alert.
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreaker:
    """Circuit breaker for S3 operations - trips after N consecutive failures."""

    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    state_file: Optional[Path] = None
    alert_callback: Optional[Callable[[str], None]] = None

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _trip_time: Optional[float] = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Load persisted state if available."""
        if self.state_file and self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self._state = CircuitState(data.get("state", "CLOSED"))
                self._failure_count = data.get("failure_count", 0)
                self._trip_time = data.get("trip_time")
            except (json.JSONDecodeError, KeyError, ValueError):
                logging.warning("Failed to load state, using defaults")

    def _save_state(self) -> None:
        """Persist circuit breaker state to file."""
        if self.state_file:
            self.state_file.write_text(json.dumps({
                "state": self._state.value,
                "failure_count": self._failure_count,
                "trip_time": self._trip_time,
            }))

    def is_allowed(self) -> bool:
        """Check if operations are allowed based on current state."""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if self._trip_time and (time.time() - self._trip_time >= self.recovery_timeout):
                self._state = CircuitState.HALF_OPEN
                logging.info("Circuit breaker entering HALF_OPEN state")
                return True
            return False
        return True  # HALF_OPEN

    def record_success(self) -> None:
        """Record a successful operation, resetting failure count."""
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            logging.info("Circuit breaker recovered - state CLOSED")
        self._save_state()

    def record_failure(self, error_msg: str = "") -> None:
        """Record a failure, potentially tripping the circuit breaker."""
        self._failure_count += 1
        logging.warning(f"S3 failure {self._failure_count}/{self.failure_threshold}: {error_msg}")
        if self._failure_count >= self.failure_threshold and self._state != CircuitState.OPEN:
            self._trip()
        self._save_state()

    def _trip(self) -> None:
        """Trip the circuit breaker, halting operations."""
        self._state = CircuitState.OPEN
        self._trip_time = time.time()
        msg = f"Circuit breaker TRIPPED after {self.failure_threshold} consecutive S3 failures"
        logging.critical(msg)
        if self.alert_callback:
            try:
                self.alert_callback(msg)
            except Exception as e:
                logging.error(f"Alert callback failed: {e}")
        self._save_state()

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._trip_time = None
        logging.info("Circuit breaker manually reset")
        self._save_state()

    def status(self) -> dict:
        """Return current status as a dictionary."""
        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "trip_time": self._trip_time,
        }


def main(argv: Optional[list] = None) -> int:
    """Main entry point for circuit breaker CLI."""
    parser = argparse.ArgumentParser(description="Circuit breaker for S3 operations")
    parser.add_argument("--threshold", type=int, default=5,
                        help="Failures before tripping (default: 5)")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="Recovery timeout in seconds (default: 60)")
    parser.add_argument("--state-file", type=Path, help="Path to persist state")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    parser.add_argument("--reset", action="store_true", help="Reset to CLOSED state")
    parser.add_argument("--test-failure", action="store_true", help="Simulate a failure")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    cb = CircuitBreaker(
        failure_threshold=args.threshold,
        recovery_timeout=args.timeout,
        state_file=args.state_file,
        alert_callback=lambda m: logging.critical(f"ALERT: {m}"),
    )

    if args.status:
        print(json.dumps(cb.status(), indent=2))
        return 0
    if args.reset:
        cb.reset()
        print("Circuit breaker reset to CLOSED state")
        return 0
    if args.test_failure:
        cb.record_failure("Simulated failure")
        print(f"State: {cb._state.value}")
        return 0

    if cb.is_allowed():
        print(f"Circuit breaker: {cb._state.value} - operations ALLOWED")
        return 0
    print(f"Circuit breaker: {cb._state.value} - operations BLOCKED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
