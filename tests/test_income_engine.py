"""Tests for backend_stub.income_engine."""

from __future__ import annotations

import datetime as _dt

import pytest

from backend_stub.income_engine import calculate_daily_income


def _session(status: str, date: str | None = None) -> dict:
    return {
        "status": status,
        "date": date or _dt.date.today().isoformat(),
    }


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    """Directly from the spec acceptance criteria."""

    def test_one_buyer_ready_income_050(self):
        """1 BUYER_READY upload → income = $0.50"""
        sessions = [_session("BUYER_READY")]
        result = calculate_daily_income(sessions)
        assert result["total_usd"] == 0.50
        assert result["sessions_counted"] == 1
        assert result["sessions_uploaded"] == 1

    def test_ten_buyer_ready_income_500(self):
        """10 BUYER_READY → $5.00"""
        sessions = [_session("BUYER_READY") for _ in range(10)]
        result = calculate_daily_income(sessions)
        assert result["total_usd"] == 5.00
        assert result["sessions_counted"] == 10
        assert result["sessions_uploaded"] == 10

    def test_fifteen_buyer_ready_capped_at_500(self):
        """15 BUYER_READY → $5.00 (cap at 10 sessions/day)"""
        sessions = [_session("BUYER_READY") for _ in range(15)]
        result = calculate_daily_income(sessions)
        assert result["total_usd"] == 5.00
        assert result["sessions_counted"] == 10
        assert result["sessions_uploaded"] == 15

    def test_one_fail_income_0(self):
        """1 FAIL → $0"""
        sessions = [_session("FAIL")]
        result = calculate_daily_income(sessions)
        assert result["total_usd"] == 0.00
        assert result["sessions_counted"] == 1
        assert result["sessions_uploaded"] == 1


# ---------------------------------------------------------------------------
# Additional unit tests
# ---------------------------------------------------------------------------


class TestRateCard:
    """Verify each rate-card tier."""

    def test_buyer_ready_rate(self):
        result = calculate_daily_income([_session("BUYER_READY")])
        assert result["total_usd"] == 0.50

    def test_strict_gates_pass_synthetic_rate(self):
        result = calculate_daily_income([_session("STRICT_GATES_PASS_SYNTHETIC")])
        assert result["total_usd"] == 0.10

    def test_fail_rate(self):
        result = calculate_daily_income([_session("FAIL")])
        assert result["total_usd"] == 0.00

    def test_unknown_status_rate(self):
        result = calculate_daily_income([_session("UNKNOWN_STATUS")])
        assert result["total_usd"] == 0.00


class TestMixedSessions:
    """Mixed-status sessions within the cap."""

    def test_mixed_statuses(self):
        sessions = [
            _session("BUYER_READY"),
            _session("BUYER_READY"),
            _session("STRICT_GATES_PASS_SYNTHETIC"),
            _session("FAIL"),
        ]
        result = calculate_daily_income(sessions)
        # 0.50 + 0.50 + 0.10 + 0.00 = 1.10
        assert result["total_usd"] == 1.10
        assert result["sessions_counted"] == 4

    def test_cap_with_mixed_statuses(self):
        """10 sessions with mixed types – cap still applies."""
        sessions = (
            [_session("BUYER_READY")] * 5
            + [_session("STRICT_GATES_PASS_SYNTHETIC")] * 5
            + [_session("BUYER_READY")] * 5  # these should be ignored
        )
        result = calculate_daily_income(sessions)
        # 5 * 0.50 + 5 * 0.10 = 2.50 + 0.50 = 3.00
        assert result["total_usd"] == 3.00
        assert result["sessions_counted"] == 10
        assert result["sessions_uploaded"] == 15


class TestEdgeCases:
    """Boundary and edge cases."""

    def test_empty_sessions(self):
        result = calculate_daily_income([])
        assert result["total_usd"] == 0.00
        assert result["sessions_counted"] == 0
        assert result["sessions_uploaded"] == 0
        assert result["date"] == "unknown"

    def test_exactly_ten_sessions(self):
        sessions = [_session("BUYER_READY") for _ in range(10)]
        result = calculate_daily_income(sessions)
        assert result["total_usd"] == 5.00
        assert result["sessions_counted"] == 10

    def test_eleven_sessions(self):
        sessions = [_session("BUYER_READY") for _ in range(11)]
        result = calculate_daily_income(sessions)
        assert result["total_usd"] == 5.00
        assert result["sessions_counted"] == 10
        assert result["sessions_uploaded"] == 11

    def test_all_fail_sessions(self):
        sessions = [_session("FAIL") for _ in range(20)]
        result = calculate_daily_income(sessions)
        assert result["total_usd"] == 0.00
        assert result["sessions_counted"] == 10  # still counted toward cap
        assert result["sessions_uploaded"] == 20

    def test_date_from_first_session(self):
        today = _dt.date.today().isoformat()
        sessions = [_session("BUYER_READY", date=today)]
        result = calculate_daily_income(sessions)
        assert result["date"] == today

    def test_currency_is_usd(self):
        result = calculate_daily_income([_session("BUYER_READY")])
        assert result["currency"] == "USD"

    def test_rounding(self):
        """Ensure total_usd is rounded to 2 decimal places."""
        # 3 * 0.10 = 0.30 – should be exact
        sessions = [_session("STRICT_GATES_PASS_SYNTHETIC") for _ in range(3)]
        result = calculate_daily_income(sessions)
        assert result["total_usd"] == 0.30
