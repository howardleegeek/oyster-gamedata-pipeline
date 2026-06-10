"""
backend_stub.income_engine – calculate daily income from session uploads.

Rate card (mock):
  BUYER_READY                    → $0.50 per session
  STRICT_GATES_PASS_SYNTHETIC    → $0.10 per session (training data, lower tier)
  FAIL                           → $0.00
  Any other status               → $0.00

Business rules:
  - Maximum 10 sessions per day count toward income (cap).
  - Sessions are counted in the order they appear; once 10 are reached,
    additional sessions contribute $0.
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Rate card
# ---------------------------------------------------------------------------
RATE_CARD: Dict[str, float] = {
    "BUYER_READY": 0.50,
    "STRICT_GATES_PASS_SYNTHETIC": 0.10,
    "FAIL": 0.00,
}

MAX_SESSIONS_PER_DAY = 10


def _rate_for_status(status: str) -> float:
    """Return the dollar rate for a given session status."""
    return RATE_CARD.get(status, 0.00)


def calculate_daily_income(
    sessions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Calculate income for a single day from a list of session dicts.

    Each session dict must have at least a ``"status"`` key.

    Returns a dict with:
      - date: the date string (from the first session, or "unknown")
      - total_usd: total income (capped at MAX_SESSIONS_PER_DAY)
      - sessions_uploaded: total number of sessions provided
      - sessions_counted: number of sessions that counted toward income
      - currency: "USD"
    """
    today = "unknown"
    total_usd = 0.0
    sessions_counted = 0

    for session in sessions:
        status = session.get("status", "FAIL")
        if sessions_counted >= MAX_SESSIONS_PER_DAY:
            break
        rate = _rate_for_status(status)
        total_usd += rate
        sessions_counted += 1
        if today == "unknown":
            today = session.get("date", "unknown")

    return {
        "date": today,
        "total_usd": round(total_usd, 2),
        "sessions_uploaded": len(sessions),
        "sessions_counted": sessions_counted,
        "currency": "USD",
    }
