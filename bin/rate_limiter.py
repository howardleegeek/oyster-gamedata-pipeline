#!/usr/bin/env python3
"""
Rate Limiter: Token-bucket per vendor key.
Production utility to enforce per-day clip budget on adapter side.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = "rate_limiter_state.json"
DEFAULT_DAILY_BUDGET = 1000
DEFAULT_REFILL_RATE = DEFAULT_DAILY_BUDGET / 86400


class TokenBucket:
    """Thread-safe token bucket for rate limiting."""

    def __init__(self, capacity: int = DEFAULT_DAILY_BUDGET, tokens: float | None = None,
                 last_refill: float | None = None, refill_rate: float = DEFAULT_REFILL_RATE) -> None:
        self.capacity = capacity
        self.tokens = tokens if tokens is not None else float(capacity)
        self.last_refill = last_refill or time.time()
        self.refill_rate = refill_rate
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.time()
        self.tokens = min(self.capacity, self.tokens + (now - self.last_refill) * self.refill_rate)
        self.last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def get_available(self) -> float:
        with self._lock:
            self._refill()
            return self.tokens

    def reset(self) -> None:
        with self._lock:
            self.tokens = float(self.capacity)
            self.last_refill = time.time()

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {"capacity": self.capacity, "tokens": self.tokens,
                    "last_refill": self.last_refill, "refill_rate": self.refill_rate}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenBucket:
        return cls(data["capacity"], data["tokens"], data["last_refill"], data["refill_rate"])


class VendorRateLimiter:
    """Manages token buckets for multiple vendors with JSON persistence."""

    def __init__(self, state_file: Path | str | None = None, default_budget: int = DEFAULT_DAILY_BUDGET) -> None:
        self.state_file = Path(state_file or DEFAULT_STATE_FILE)
        self.default_budget = default_budget
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.RLock()
        self._load_state()

    def _load_state(self) -> None:
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    data = json.load(f)
                with self._lock:
                    for vid, bd in data.get("buckets", {}).items():
                        self._buckets[vid] = TokenBucket.from_dict(bd)
            except (json.JSONDecodeError, KeyError) as e:
                logger.debug("rate_limiter: corrupt state file %s, starting fresh: %s",
                             self.state_file, e)

    def save_state(self) -> None:
        with self._lock:
            data = {"buckets": {vid: b.to_dict() for vid, b in self._buckets.items()},
                    "saved_at": datetime.now().isoformat()}
            tmp = self.state_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.state_file)

    def get_bucket(self, vendor_id: str) -> TokenBucket:
        with self._lock:
            if vendor_id not in self._buckets:
                self._buckets[vendor_id] = TokenBucket(capacity=self.default_budget)
            return self._buckets[vendor_id]

    def consume(self, vendor_id: str, tokens: float = 1.0) -> bool:
        result = self.get_bucket(vendor_id).consume(tokens)
        self.save_state()
        return result

    def get_remaining(self, vendor_id: str) -> float:
        return self.get_bucket(vendor_id).get_available()

    def reset_vendor(self, vendor_id: str) -> None:
        self.get_bucket(vendor_id).reset()
        self.save_state()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Token-bucket rate limiter for vendor clip budgets.")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE, help="State JSON file")
    parser.add_argument("--budget", type=int, default=DEFAULT_DAILY_BUDGET, help="Daily token budget")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="Check if tokens available")
    p.add_argument("vendor_id")
    p.add_argument("--tokens", type=float, default=1.0)
    p = sub.add_parser("consume", help="Consume tokens")
    p.add_argument("vendor_id")
    p.add_argument("--tokens", type=float, default=1.0)
    p = sub.add_parser("status", help="Show vendor status")
    p.add_argument("vendor_id")
    p = sub.add_parser("reset", help="Reset vendor bucket")
    p.add_argument("vendor_id")

    args = parser.parse_args(argv)
    limiter = VendorRateLimiter(state_file=args.state_file, default_budget=args.budget)

    if args.command == "check":
        ok = limiter.get_remaining(args.vendor_id) >= args.tokens
        print(f"{'OK' if ok else 'LIMIT'}: {limiter.get_remaining(args.vendor_id):.1f}/{args.budget}")
        return 0 if ok else 1
    elif args.command == "consume":
        ok = limiter.consume(args.vendor_id, args.tokens)
        print(f"{'OK' if ok else 'LIMIT'}: {limiter.get_remaining(args.vendor_id):.1f} left")
        return 0 if ok else 1
    elif args.command == "status":
        print(f"Vendor: {args.vendor_id}, Remaining: {limiter.get_remaining(args.vendor_id):.1f}/{args.budget}")
        return 0
    elif args.command == "reset":
        limiter.reset_vendor(args.vendor_id)
        print(f"Reset vendor {args.vendor_id}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
