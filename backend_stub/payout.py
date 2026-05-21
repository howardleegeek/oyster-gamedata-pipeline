"""
payout.py — PayPal/Stripe payout simulator with 24 h SLA (ISC-5).

State machine:
    queued → processing → paid
    queued → failed

Worker thread advances states on a schedule.  In production the schedule
is every 5 min; with ``--accelerate N`` the wall-clock is compressed N×
so that 1 h of simulated time passes in 3600/N real seconds.

Daily limit per user: $1000 (mock).
"""

from __future__ import annotations

import argparse
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAILY_LIMIT_USD: float = 1000.0
QUEUED_TO_PROCESSING_HOURS: float = 1.0  # 1 h before processing
PROCESSING_TO_PAID_MINUTES: float = 30.0  # 30 min before paid
WORKER_INTERVAL_SECONDS: float = 300.0  # 5 min default

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

VALID_STATUSES = {"queued", "processing", "paid", "failed"}


@dataclass
class PayoutRecord:
    """In-memory representation of a single payout."""

    id: str
    user_id: str
    amount_usd: float
    status: str = "queued"
    queued_at: Optional[datetime] = None
    processing_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    txn_id: Optional[str] = None
    est_arrival: Optional[datetime] = None
    provider: str = "paypal"  # mock: "paypal" | "stripe"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "user_id": self.user_id,
            "amount_usd": self.amount_usd,
            "status": self.status,
            "queued_at": _iso(self.queued_at),
            "provider": self.provider,
        }
        if self.processing_at:
            d["processing_at"] = _iso(self.processing_at)
        if self.paid_at:
            d["paid_at"] = _iso(self.paid_at)
            d["txn_id"] = self.txn_id
        if self.failed_at:
            d["failed_at"] = _iso(self.failed_at)
            d["failure_reason"] = self.failure_reason
        if self.est_arrival:
            d["est_arrival"] = _iso(self.est_arrival)
        return d


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class PayoutStore:
    """Thread-safe in-memory dict store for payouts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._payouts: Dict[str, PayoutRecord] = {}
        # user_id -> {date_str -> total_usd}
        self._daily_totals: Dict[str, Dict[str, float]] = {}

    # -- public API -----------------------------------------------------------

    def create(
        self,
        user_id: str,
        amount_usd: float,
        provider: str = "paypal",
    ) -> PayoutRecord:
        now = _now()
        payout_id = _gen_id()
        est = now + timedelta(hours=24)  # 24 h SLA
        record = PayoutRecord(
            id=payout_id,
            user_id=user_id,
            amount_usd=amount_usd,
            status="queued",
            queued_at=now,
            est_arrival=est,
            provider=provider,
        )
        with self._lock:
            self._payouts[payout_id] = record
        return record

    def get(self, payout_id: str) -> Optional[PayoutRecord]:
        with self._lock:
            return self._payouts.get(payout_id)

    def list_all(self) -> list[PayoutRecord]:
        with self._lock:
            return list(self._payouts.values())

    def force_paid(self, payout_id: str) -> Optional[PayoutRecord]:
        """Admin helper: immediately mark a payout as paid."""
        with self._lock:
            rec = self._payouts.get(payout_id)
            if rec is None:
                return None
            rec.status = "paid"
            rec.processing_at = _now()
            rec.paid_at = _now()
            rec.txn_id = f"mock-txn-{uuid.uuid4().hex[:12]}"
            return rec

    def force_failed(self, payout_id: str, reason: str = "mock_failure") -> Optional[PayoutRecord]:
        """Admin helper: immediately mark a payout as failed."""
        with self._lock:
            rec = self._payouts.get(payout_id)
            if rec is None:
                return None
            rec.status = "failed"
            rec.failed_at = _now()
            rec.failure_reason = reason
            return rec

    # -- daily limit tracking -------------------------------------------------

    def daily_spent(self, user_id: str) -> float:
        today = _now().strftime("%Y-%m-%d")
        with self._lock:
            return self._daily_totals.get(user_id, {}).get(today, 0.0)

    def can_payout(self, user_id: str, amount_usd: float) -> bool:
        return (self.daily_spent(user_id) + amount_usd) <= DAILY_LIMIT_USD

    def record_daily(self, user_id: str, amount_usd: float) -> None:
        today = _now().strftime("%Y-%m-%d")
        with self._lock:
            self._daily_totals.setdefault(user_id, {})[today] = (
                self._daily_totals.get(user_id, {}).get(today, 0.0) + amount_usd
            )

    # -- worker helpers -------------------------------------------------------

    def get_queued(self) -> list[PayoutRecord]:
        with self._lock:
            return [p for p in self._payouts.values() if p.status == "queued"]

    def get_processing(self) -> list[PayoutRecord]:
        with self._lock:
            return [p for p in self._payouts.values() if p.status == "processing"]

    def advance_queued(self, threshold_seconds: float) -> int:
        """Move queued → processing if older than threshold."""
        now = _now()
        count = 0
        with self._lock:
            for p in self._payouts.values():
                if p.status == "queued" and p.queued_at:
                    age = (now - p.queued_at).total_seconds()
                    if age >= threshold_seconds:
                        p.status = "processing"
                        p.processing_at = now
                        count += 1
        return count

    def advance_processing(self, threshold_seconds: float) -> int:
        """Move processing → paid if older than threshold."""
        now = _now()
        count = 0
        with self._lock:
            for p in self._payouts.values():
                if p.status == "processing" and p.processing_at:
                    age = (now - p.processing_at).total_seconds()
                    if age >= threshold_seconds:
                        p.status = "paid"
                        p.paid_at = now
                        p.txn_id = f"mock-txn-{uuid.uuid4().hex[:12]}"
                        count += 1
        return count


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------


class PayoutWorker:
    """Background thread that advances payout states on a schedule."""

    def __init__(
        self,
        store: PayoutStore,
        accelerate: float = 1.0,
        interval: float = WORKER_INTERVAL_SECONDS,
    ) -> None:
        self.store = store
        self.accelerate = max(accelerate, 1.0)
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def effective_queued_threshold(self) -> float:
        """Seconds a payout must sit in queued before → processing."""
        return QUEUED_TO_PROCESSING_HOURS * 3600 / self.accelerate

    @property
    def effective_processing_threshold(self) -> float:
        """Seconds a payout must sit in processing before → paid."""
        return PROCESSING_TO_PAID_MINUTES * 60 / self.accelerate

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="payout-worker")
        self._thread.start()
        logger.info(
            "Payout worker started (accelerate=%s, interval=%ss)",
            self.accelerate,
            self._interval,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info("Payout worker stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                q_count = self.store.advance_queued(self.effective_queued_threshold)
                p_count = self.store.advance_processing(self.effective_processing_threshold)
                if q_count or p_count:
                    logger.info(
                        "Worker tick: %d queued→processing, %d processing→paid",
                        q_count,
                        p_count,
                    )
            except Exception:
                logger.exception("Worker tick error")
            self._stop_event.wait(self._interval)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _gen_id() -> str:
    return f"po-{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# CLI / argparse (for --accelerate flag)
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Payout simulator worker")
    parser.add_argument(
        "--accelerate",
        type=float,
        default=1.0,
        help="Time compression factor (default 1.0 = real-time)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=WORKER_INTERVAL_SECONDS,
        help="Worker loop interval in seconds (default 300)",
    )
    return parser.parse_args(argv)
