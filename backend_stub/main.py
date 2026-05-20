"""
main.py — FastAPI application for the gamedata-pipeline backend stub.

Mounts the payout simulator endpoints under /api/v1/payouts.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from backend_stub.payout import (
    DAILY_LIMIT_USD,
    PayoutStore,
    PayoutWorker,
    parse_args,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
store = PayoutStore()
worker: PayoutWorker | None = None

# ---------------------------------------------------------------------------
# Auth helpers (mock)
# ---------------------------------------------------------------------------
ADMIN_TOKEN = "admin-secret-token"  # mock admin token
BEARER_USERS: Dict[str, str] = {
    "user-token-1": "user-001",
    "user-token-2": "user-002",
    "user-token-3": "user-003",
}


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _require_bearer(request: Request) -> str:
    token = _extract_bearer(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    user_id = BEARER_USERS.get(token)
    if user_id is None:
        # Accept any unknown bearer token as a generic user for testing
        return f"anon-{token[:8]}"
    return user_id


def _require_admin(request: Request) -> None:
    token = _extract_bearer(request)
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin token required")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker
    accel = getattr(app, "_accelerate", 1.0)
    interval = getattr(app, "_interval", 300.0)
    worker = PayoutWorker(store, accelerate=accel, interval=interval)
    worker.start()
    logger.info("Payout worker started with accelerate=%s", accel)
    yield
    if worker:
        worker.stop()
    logger.info("Payout worker stopped")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(accelerate: float = 1.0, interval: float = 300.0) -> FastAPI:
    app = FastAPI(
        title="GameData Payout Simulator",
        version="0.1.0",
        lifespan=lifespan,
    )
    app._accelerate = accelerate  # type: ignore[attr-defined]
    app._interval = interval  # type: ignore[attr-defined]
    return app


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def register_routes(app: FastAPI) -> None:

    @app.post("/api/v1/payouts/queue")
    async def queue_payout(request: Request):
        """Queue a new payout. Returns payout_id, queued_at, est_arrival."""
        user_id = _require_bearer(request)

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        amount_usd = body.get("amount_usd")
        if amount_usd is None:
            raise HTTPException(status_code=400, detail="amount_usd is required")

        try:
            amount_usd = float(amount_usd)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="amount_usd must be a number")

        if amount_usd <= 0:
            raise HTTPException(status_code=400, detail="amount_usd must be positive")

        provider = body.get("provider", "paypal")
        if provider not in ("paypal", "stripe"):
            raise HTTPException(
                status_code=400, detail="provider must be 'paypal' or 'stripe'"
            )

        # Daily limit check
        if not store.can_payout(user_id, amount_usd):
            spent = store.daily_spent(user_id)
            remaining = DAILY_LIMIT_USD - spent
            retry_after = _seconds_until_midnight()
            return JSONResponse(
                status_code=429,
                content={
                    "error": "daily_limit_exceeded",
                    "detail": f"Daily payout limit of ${DAILY_LIMIT_USD} exceeded. "
                    f"Spent: ${spent:.2f}, remaining: ${remaining:.2f}",
                    "retry_after": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        record = store.create(user_id, amount_usd, provider)
        store.record_daily(user_id, amount_usd)

        return {
            "payout_id": record.id,
            "queued_at": record.queued_at.isoformat() if record.queued_at else None,
            "est_arrival": (
                record.est_arrival.isoformat() if record.est_arrival else None
            ),
            "amount_usd": record.amount_usd,
            "provider": record.provider,
        }

    @app.get("/api/v1/payouts/{payout_id}")
    async def get_payout(payout_id: str, request: Request):
        """Get payout status by ID."""
        _require_bearer(request)

        record = store.get(payout_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Payout not found")

        return record.to_dict()

    @app.post("/api/v1/payouts/{payout_id}/simulate")
    async def simulate_payout(payout_id: str, request: Request):
        """Admin endpoint: immediately mark a payout as paid (for testing)."""
        _require_admin(request)

        record = store.force_paid(payout_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Payout not found")

        return record.to_dict()

    @app.post("/api/v1/payouts/{payout_id}/simulate-fail")
    async def simulate_fail_payout(payout_id: str, request: Request):
        """Admin endpoint: immediately mark a payout as failed (for testing)."""
        _require_admin(request)

        body = (
            await request.json()
            if request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        reason = body.get("reason", "mock_failure")

        record = store.force_failed(payout_id, reason)
        if record is None:
            raise HTTPException(status_code=404, detail="Payout not found")

        return record.to_dict()

    @app.get("/api/v1/payouts")
    async def list_payouts(request: Request):
        """List all payouts (admin only)."""
        _require_admin(request)
        payouts = store.list_all()
        return [p.to_dict() for p in payouts]

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "worker_running": worker is not None
            and worker._thread is not None
            and worker._thread.is_alive(),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seconds_until_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + __import__("datetime").timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((tomorrow - now).total_seconds())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    import uvicorn

    app = create_app(accelerate=args.accelerate, interval=args.interval)
    register_routes(app)

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
