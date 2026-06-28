"""
defense_obs_auth.py — Blue-team OBS auth helper (G093 defense).

SHA256+base64 credential signing with sliding-window rate-limiter
that blocks after configurable consecutive failed attempts.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import logging
import sys
import time
from collections import defaultdict

log = logging.getLogger(__name__)


class RateLimiter:
    """Sliding-window rate-limiter tracking consecutive failed attempts."""

    def __init__(
        self, max_failures: int = 5, window_seconds: float = 60.0, cooldown_seconds: float = 300.0
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._cooldown_until: dict[str, float] = {}

    def record_failure(self, identity: str) -> None:
        """Record a failed auth attempt for *identity*."""
        now = time.monotonic()
        self._failures[identity].append(now)
        cutoff = now - self.window_seconds
        self._failures[identity] = [t for t in self._failures[identity] if t > cutoff]
        if len(self._failures[identity]) >= self.max_failures:
            self._cooldown_until[identity] = now + self.cooldown_seconds
            log.warning("Rate-limit triggered for %s", identity)

    def is_blocked(self, identity: str) -> bool:
        """Return True if *identity* is currently rate-limited."""
        now = time.monotonic()
        if identity in self._cooldown_until:
            if now < self._cooldown_until[identity]:
                return True
            del self._cooldown_until[identity]
            self._failures.pop(identity, None)
        return False

    def reset(self, identity: str) -> None:
        """Clear failure history for *identity*."""
        self._failures.pop(identity, None)
        self._cooldown_until.pop(identity, None)

    def status(self, identity: str) -> tuple[bool, int, float | None]:
        """Return (is_blocked, failure_count, seconds_remaining_in_cooldown)."""
        blocked = self.is_blocked(identity)
        count = len(self._failures.get(identity, []))
        remaining = None
        if blocked and identity in self._cooldown_until:
            remaining = max(0.0, self._cooldown_until[identity] - time.monotonic())
        return blocked, count, remaining


_global_limiter = RateLimiter()


def sign_payload(secret: str, payload: str) -> str:
    """Return base64(SHA256(secret || payload)) as a URL-safe string."""
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_signature(secret: str, payload: str, signature: str, identity: str = "default") -> bool:
    """Verify a signature against the expected value with rate-limiting."""
    if _global_limiter.is_blocked(identity):
        log.warning("Auth attempt blocked for identity: %s", identity)
        return False

    expected = sign_payload(secret, payload)
    if len(signature) != len(expected):
        _global_limiter.record_failure(identity)
        return False

    # Constant-time comparison to prevent timing attacks
    result = sum(ord(a) ^ ord(b) for a, b in zip(signature, expected, strict=True))
    if result != 0:
        _global_limiter.record_failure(identity)
        return False

    _global_limiter.reset(identity)
    return True


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for OBS auth helper."""
    parser = argparse.ArgumentParser(
        prog="defense_obs_auth",
        description="OBS auth helper with SHA256+base64 signing and rate-limiting",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sign_parser = subparsers.add_parser("sign", help="Sign a payload")
    sign_parser.add_argument("--secret", required=True, help="Secret key")
    sign_parser.add_argument("--payload", required=True, help="Payload to sign")

    verify_parser = subparsers.add_parser("verify", help="Verify a signature")
    verify_parser.add_argument("--secret", required=True, help="Secret key")
    verify_parser.add_argument("--payload", required=True, help="Original payload")
    verify_parser.add_argument("--sig", required=True, help="Signature to verify")
    verify_parser.add_argument("--identity", default="default", help="Client identity")

    rate_parser = subparsers.add_parser("check-rate", help="Check rate-limit status")
    rate_parser.add_argument("--identity", required=True, help="Client identity")

    args = parser.parse_args(argv)

    if args.command == "sign":
        print(sign_payload(args.secret, args.payload))
        return 0

    if args.command == "verify":
        if verify_signature(args.secret, args.payload, args.sig, args.identity):
            print("VALID")
            return 0
        print("INVALID")
        return 1

    if args.command == "check-rate":
        blocked, count, remaining = _global_limiter.status(args.identity)
        if remaining:
            print(f"blocked={blocked} failures={count} remaining={remaining:.1f}s")
        else:
            print(f"blocked={blocked} failures={count}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
