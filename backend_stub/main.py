"""
backend_stub.main – local FastAPI server for recorder integration testing.

Endpoints:
  POST /api/v1/auth/google/exchange   → mock OAuth exchange
  POST /api/v1/auth/discord/exchange  → mock OAuth exchange
  GET  /api/v1/income/today           → today's income summary
  POST /api/v1/upload/signed-url      → mock S3 presigned URL
  POST /api/v1/sessions               → register a session
  POST /api/v1/testers/apply          → apply for beta access
  GET  /api/v1/testers                → list all applicants (admin)
  POST /api/v1/testers/{id}/approve   → approve + return signed download URL
  POST /api/v1/testers/{id}/reject    → reject application

All data lives in memory (dicts). No DB, no external services.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import uuid
from typing import Any, Dict, List

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from backend_stub import income_engine, tester_invite

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
_sessions_store: Dict[str, Any] = {}

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="gamedata-pipeline backend stub", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://localhost(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_bearer(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401, detail="Missing or invalid Bearer token"
            )
        return authorization[7:]  # strip "Bearer "

    # ------------------------------------------------------------------
    # Auth endpoints (mock)
    # ------------------------------------------------------------------
    @app.post("/api/v1/auth/google/exchange")
    async def auth_google_exchange(request: Request):
        await request.json()
        return {
            "access_token": f"mock-google-at-{uuid.uuid4().hex[:16]}",
            "refresh_token": f"mock-google-rt-{uuid.uuid4().hex[:16]}",
            "expires_in": 3600,
        }

    @app.post("/api/v1/auth/discord/exchange")
    async def auth_discord_exchange(request: Request):
        await request.json()
        return {
            "access_token": f"mock-discord-at-{uuid.uuid4().hex[:16]}",
            "refresh_token": f"mock-discord-rt-{uuid.uuid4().hex[:16]}",
            "expires_in": 3600,
        }

    # ------------------------------------------------------------------
    # Income endpoint – uses income_engine
    # ------------------------------------------------------------------
    @app.get("/api/v1/income/today")
    async def income_today(authorization: str | None = Header(default=None)):
        _require_bearer(authorization)
        today = _dt.date.today().isoformat()
        # Gather today's sessions from the in-memory store
        today_sessions: List[Dict[str, Any]] = [
            s for s in _sessions_store.values() if s.get("date") == today
        ]
        return income_engine.calculate_daily_income(today_sessions)

    # ------------------------------------------------------------------
    # Upload signed URL (mock S3 presigned URL)
    # ------------------------------------------------------------------
    @app.post("/api/v1/upload/signed-url")
    async def upload_signed_url(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        _require_bearer(authorization)
        body = await request.json()
        key = body.get("key", f"uploads/{uuid.uuid4().hex}.bin")
        expires_at = (
            _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
        ).isoformat()
        return {
            "url": f"https://mock-s3.example.com/{key}?X-Amz-Signature=fake",
            "expires_at": expires_at,
            "key": key,
        }

    # ------------------------------------------------------------------
    # Sessions endpoint
    # ------------------------------------------------------------------
    @app.post("/api/v1/sessions")
    async def create_session(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        _require_bearer(authorization)
        body = await request.json()
        session_id = body.get("session_id", str(uuid.uuid4()))
        _sessions_store[session_id] = {
            "session_id": session_id,
            "status": body.get("status", "received"),
            "date": body.get("date", _dt.date.today().isoformat()),
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        return {"session_id": session_id, "status": "received"}

    # ------------------------------------------------------------------
    # Tester invite endpoints
    # ------------------------------------------------------------------
    app.include_router(tester_invite.router)

    return app


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="gamedata-pipeline backend stub")
    parser.add_argument(
        "--port", type=int, default=8500, help="Port to listen on (default: 8500)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind (default: 0.0.0.0)",
    )
    args = parser.parse_args()

    import uvicorn

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
