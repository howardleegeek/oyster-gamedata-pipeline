"""Tests for bin/rate_limiter.py — token-bucket per-vendor rate limiter.

Covers:
  * TokenBucket: refill math, consume, get_available, reset, to_dict/from_dict round-trip
  * VendorRateLimiter: per-vendor isolation, state-file persistence (incl. corrupt/empty JSON),
    get_remaining, reset_vendor, default budget propagation
  * main() CLI: check / consume / status / reset exit codes and output
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the bin module is importable as a top-level name (matches sibling tests).
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import rate_limiter as rl  # noqa: E402

# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TestTokenBucketBasics:
    def test_default_capacity_matches_module_constant(self):
        bucket = rl.TokenBucket()
        # tokens default to capacity when not provided
        assert bucket.capacity == rl.DEFAULT_DAILY_BUDGET
        assert bucket.tokens == float(rl.DEFAULT_DAILY_BUDGET)

    def test_explicit_tokens_and_last_refill_kept(self):
        bucket = rl.TokenBucket(capacity=5, tokens=2.5, last_refill=100.0, refill_rate=1.0)
        assert bucket.capacity == 5
        assert bucket.tokens == 2.5
        assert bucket.last_refill == 100.0
        assert bucket.refill_rate == 1.0

    def test_consume_decrements_tokens(self):
        bucket = rl.TokenBucket(capacity=10, tokens=10.0, last_refill=0.0, refill_rate=0.0)
        assert bucket.consume(3) is True
        assert bucket.tokens == 7.0

    def test_consume_returns_false_when_insufficient(self):
        bucket = rl.TokenBucket(capacity=10, tokens=2.0, last_refill=0.0, refill_rate=0.0)
        assert bucket.consume(5) is False
        # Tokens unchanged on rejection
        assert bucket.tokens == 2.0

    def test_consume_default_is_one_token(self):
        bucket = rl.TokenBucket(capacity=10, tokens=10.0, last_refill=0.0, refill_rate=0.0)
        assert bucket.consume() is True
        assert bucket.tokens == 9.0

    def test_consume_uses_lock(self):
        bucket = rl.TokenBucket(capacity=10, tokens=10.0, last_refill=0.0, refill_rate=0.0)
        with mock.patch.object(bucket, "_lock") as fake_lock:
            fake_lock.__enter__.return_value = None
            fake_lock.__exit__.return_value = False
            bucket.consume(1)
            assert fake_lock.__enter__.called
            assert fake_lock.__exit__.called


class TestTokenBucketRefill:
    def test_refill_caps_at_capacity(self):
        # Set last_refill to a known past time; refill at real-time
        # would push tokens past capacity. NOTE: do not use 0.0 — the
        # constructor's `last_refill or time.time()` treats 0.0 as falsy
        # and overrides it with real time.
        bucket = rl.TokenBucket(
            capacity=5,
            tokens=4.0,
            last_refill=100.0,
            refill_rate=1.0,  # 1 token / second
        )
        with mock.patch("rate_limiter.time.time", return_value=1100.0):
            bucket._refill()
        # 4.0 + (1100 - 100) * 1.0 = 1004.0, but capped at capacity 5
        assert bucket.tokens == 5.0

    def test_refill_adds_proportional_amount(self):
        bucket = rl.TokenBucket(
            capacity=10,
            tokens=2.0,
            last_refill=100.0,
            refill_rate=2.0,  # 2 tokens / sec
        )
        with mock.patch("rate_limiter.time.time", return_value=103.0):
            bucket._refill()
        # 2.0 + (103 - 100) * 2.0 = 8.0
        assert bucket.tokens == 8.0

    def test_get_available_refills_before_returning(self):
        bucket = rl.TokenBucket(
            capacity=10,
            tokens=5.0,
            last_refill=100.0,
            refill_rate=1.0,
        )
        with mock.patch("rate_limiter.time.time", return_value=102.0):
            assert bucket.get_available() == 7.0

    def test_reset_returns_to_full_capacity(self):
        bucket = rl.TokenBucket(
            capacity=10, tokens=1.0, last_refill=1.0, refill_rate=0.0
        )
        with mock.patch("rate_limiter.time.time", return_value=999.0):
            bucket.reset()
        assert bucket.tokens == 10.0
        assert bucket.last_refill == 999.0


class TestTokenBucketSerialization:
    def test_to_dict_returns_all_fields(self):
        bucket = rl.TokenBucket(
            capacity=7, tokens=3.0, last_refill=42.0, refill_rate=0.5
        )
        out = bucket.to_dict()
        assert out == {
            "capacity": 7,
            "tokens": 3.0,
            "last_refill": 42.0,
            "refill_rate": 0.5,
        }

    def test_from_dict_round_trip(self):
        original = rl.TokenBucket(
            capacity=7, tokens=3.0, last_refill=42.0, refill_rate=0.5
        )
        restored = rl.TokenBucket.from_dict(original.to_dict())
        assert restored.capacity == original.capacity
        assert restored.tokens == original.tokens
        assert restored.last_refill == original.last_refill
        assert restored.refill_rate == original.refill_rate


# ---------------------------------------------------------------------------
# VendorRateLimiter
# ---------------------------------------------------------------------------


class TestVendorRateLimiter:
    def test_first_get_bucket_creates_with_default_budget(self, tmp_path):
        limiter = rl.VendorRateLimiter(state_file=tmp_path / "s.json", default_budget=42)
        bucket = limiter.get_bucket("v1")
        assert isinstance(bucket, rl.TokenBucket)
        assert bucket.capacity == 42
        assert limiter._buckets == {"v1": bucket}

    def test_get_bucket_returns_same_instance_for_same_vendor(self, tmp_path):
        limiter = rl.VendorRateLimiter(state_file=tmp_path / "s.json", default_budget=10)
        b1 = limiter.get_bucket("v1")
        b2 = limiter.get_bucket("v1")
        assert b1 is b2

    def test_get_bucket_isolates_vendors(self, tmp_path):
        limiter = rl.VendorRateLimiter(state_file=tmp_path / "s.json", default_budget=5)
        b1 = limiter.get_bucket("v1")
        b2 = limiter.get_bucket("v2")
        assert b1 is not b2
        assert limiter._buckets.keys() == {"v1", "v2"}

    def test_consume_saves_state_after_call(self, tmp_path):
        state = tmp_path / "s.json"
        limiter = rl.VendorRateLimiter(state_file=state, default_budget=5)
        with mock.patch("rate_limiter.time.time", return_value=100.0):
            assert limiter.consume("v1", 1) is True
        assert state.exists()
        data = json.loads(state.read_text())
        assert "buckets" in data
        assert "v1" in data["buckets"]
        assert "saved_at" in data

    def test_consume_returns_false_when_over_budget(self, tmp_path):
        state = tmp_path / "s.json"
        limiter = rl.VendorRateLimiter(state_file=state, default_budget=2)
        with mock.patch("rate_limiter.time.time", return_value=0.0):
            assert limiter.consume("v1", 2) is True
            assert limiter.consume("v1", 1) is False

    def test_get_remaining_reflects_state(self, tmp_path):
        limiter = rl.VendorRateLimiter(state_file=tmp_path / "s.json", default_budget=10)
        with mock.patch("rate_limiter.time.time", return_value=0.0):
            assert limiter.get_remaining("v1") == 10.0
            limiter.consume("v1", 3)
            assert limiter.get_remaining("v1") == 7.0

    def test_reset_vendor_refills_and_saves(self, tmp_path):
        state = tmp_path / "s.json"
        limiter = rl.VendorRateLimiter(state_file=state, default_budget=10)
        # Pre-consume to deplete
        with mock.patch("rate_limiter.time.time", return_value=1_000_000.0):
            limiter.consume("v1", 5)
            assert limiter.get_remaining("v1") == 5.0
        # Reset under a fresh time mock; save_state runs at end
        with mock.patch("rate_limiter.time.time", return_value=1_000_010.0):
            limiter.reset_vendor("v1")
            # Tokens should be refilled to capacity (refill rate is
            # DEFAULT_REFILL_RATE; since we just set last_refill to
            # the current mocked time, the next refill subtracts a tiny
            # amount but stays at capacity=10)
            assert limiter._buckets["v1"].tokens == 10.0
            assert limiter._buckets["v1"].last_refill == 1_000_010.0
        data = json.loads(state.read_text())
        assert "v1" in data["buckets"]
        assert data["buckets"]["v1"]["tokens"] == 10.0

    def test_load_state_handles_missing_file(self, tmp_path):
        # No state file at all — should not raise, _buckets remains empty
        limiter = rl.VendorRateLimiter(state_file=tmp_path / "missing.json", default_budget=10)
        assert limiter._buckets == {}

    def test_load_state_handles_corrupt_json(self, tmp_path, capsys):
        state = tmp_path / "s.json"
        state.write_text("{not valid json")
        # Should not raise
        limiter = rl.VendorRateLimiter(state_file=state, default_budget=10)
        assert limiter._buckets == {}

    def test_load_state_handles_missing_keys(self, tmp_path):
        # Valid JSON but missing "buckets" key
        state = tmp_path / "s.json"
        state.write_text("{}")
        limiter = rl.VendorRateLimiter(state_file=state, default_budget=10)
        assert limiter._buckets == {}

    def test_load_state_restores_existing_buckets(self, tmp_path):
        state = tmp_path / "s.json"
        saved = {
            "buckets": {
                "v1": {
                    "capacity": 5,
                    "tokens": 3.0,
                    "last_refill": 1.0,
                    "refill_rate": 0.1,
                }
            },
            "saved_at": "2026-01-01T00:00:00",
        }
        state.write_text(json.dumps(saved))
        limiter = rl.VendorRateLimiter(state_file=state, default_budget=999)
        assert "v1" in limiter._buckets
        bucket = limiter._buckets["v1"]
        assert bucket.capacity == 5
        assert bucket.tokens == 3.0
        assert bucket.last_refill == 1.0
        assert bucket.refill_rate == 0.1

    def test_save_state_writes_via_tmp_replace(self, tmp_path):
        # Atomic write pattern: tmp + replace
        state = tmp_path / "s.json"
        limiter = rl.VendorRateLimiter(state_file=state, default_budget=5)
        with mock.patch("rate_limiter.time.time", return_value=0.0):
            limiter.consume("v1", 1)
        # After save: tmp file should not remain
        assert not state.with_suffix(".tmp").exists()
        assert state.exists()


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


class TestMain:
    def _make_limiter(self, state_file, budget=10):
        # Helper: build a limiter with empty state so the CLI sees no leftover vendor data
        return rl.VendorRateLimiter(state_file=state_file, default_budget=budget)

    def test_check_returns_zero_when_available(self, tmp_path, capsys):
        state = tmp_path / "s.json"
        with mock.patch.object(sys, "argv", ["rate_limiter.py", "--state-file", str(state),
                                             "--budget", "10", "check", "v1"]):
            rc = rl.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert out.startswith("OK:")
        assert "/10" in out

    def test_check_returns_one_when_insufficient(self, tmp_path, capsys):
        state = tmp_path / "s.json"
        # Pre-burn through the budget
        limiter = self._make_limiter(state, budget=2)
        with mock.patch("rate_limiter.time.time", return_value=0.0):
            limiter.consume("v1", 2)
        with mock.patch.object(sys, "argv", ["rate_limiter.py", "--state-file", str(state),
                                             "--budget", "2", "check", "v1", "--tokens", "1"]):
            rc = rl.main()
        out = capsys.readouterr().out
        assert rc == 1
        assert out.startswith("LIMIT:")

    def test_consume_returns_zero_on_success(self, tmp_path, capsys):
        state = tmp_path / "s.json"
        with mock.patch.object(sys, "argv", ["rate_limiter.py", "--state-file", str(state),
                                             "--budget", "5", "consume", "v1"]):
            rc = rl.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert out.startswith("OK:")
        assert "left" in out

    def test_consume_returns_one_on_limit(self, tmp_path, capsys):
        state = tmp_path / "s.json"
        with mock.patch.object(sys, "argv", ["rate_limiter.py", "--state-file", str(state),
                                             "--budget", "1", "consume", "v1", "--tokens", "5"]):
            rc = rl.main()
        out = capsys.readouterr().out
        assert rc == 1
        assert out.startswith("LIMIT:")

    def test_status_prints_remaining(self, tmp_path, capsys):
        state = tmp_path / "s.json"
        with mock.patch.object(sys, "argv", ["rate_limiter.py", "--state-file", str(state),
                                             "--budget", "7", "status", "v1"]):
            rc = rl.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "Vendor: v1" in out
        assert "/7" in out

    def test_reset_refills_vendor(self, tmp_path, capsys):
        state = tmp_path / "s.json"
        # Pre-consume to deplete
        limiter = self._make_limiter(state, budget=3)
        with mock.patch("rate_limiter.time.time", return_value=0.0):
            limiter.consume("v1", 3)
        with mock.patch.object(sys, "argv", ["rate_limiter.py", "--state-file", str(state),
                                             "--budget", "3", "reset", "v1"]):
            rc = rl.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "Reset vendor v1" in out
        data = json.loads(state.read_text())
        assert data["buckets"]["v1"]["tokens"] == 3.0

    def test_missing_subcommand_exits_with_code_2(self, capsys):
        # argparse exits with 2 when a required subparser is missing
        with mock.patch.object(sys, "argv", ["rate_limiter.py", "--state-file",
                                             "/tmp/rl_unused.json"]):
            with pytest.raises(SystemExit) as exc_info:
                rl.main()
        assert exc_info.value.code == 2
