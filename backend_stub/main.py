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
  POST /api/sentry/store/             → accept Sentry-format crash envelopes

All data lives in memory (dicts). No DB, no external services.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import uuid
from typing import Any, Dict

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from backend_stub import appcast_server, sentry_compat, tester_invite

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
_income_store: Dict[str, Any] = {}
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
    # Health check
    # ------------------------------------------------------------------
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": "0.1.0"}

    # ------------------------------------------------------------------
    # Health check (no auth required)
    # ------------------------------------------------------------------
    @app.get('/healthz')
    async def healthz():
        return {'status': 'ok'}

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
    # Income endpoint
    # ------------------------------------------------------------------
    @app.get("/api/v1/income/today")
    async def income_today(authorization: str | None = Header(default=None)):
        _require_bearer(authorization)
        today = _dt.date.today().isoformat()
        if today not in _income_store:
            _income_store[today] = {
                "date": today,
                "total_usd": 0.0,
                "sessions_uploaded": 0,
                "currency": "USD",
            }
        return _income_store[today]

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
            "status": "received",
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        return {"session_id": session_id, "status": "received"}

    # ------------------------------------------------------------------
    # Sentry crash-reporter endpoint
    # ------------------------------------------------------------------
    @app.post("/api/sentry/store/")
    async def sentry_store(request: Request):
        """Accept a Sentry SDK envelope or plain JSON crash event.

        Parses the incoming payload, stores it in memory, and deduplicates
        by ``stack_hash``.  Returns 200 with the ``event_id``.
        """
        content_type = request.headers.get("content-type", "")

        # --- Envelope (text/plain or application/x-sentry-envelope) ---
        if "text/plain" in content_type or "x-sentry-envelope" in content_type:
            raw = await request.body()
            text = raw.decode("utf-8", errors="replace")
            events = sentry_compat.parse_envelope(text)
        # --- JSON body ---
        else:
            body = await request.json()
            events = sentry_compat.parse_json_body(body)

        if not events:
            raise HTTPException(
                status_code=400,
                detail="No parseable Sentry event found in request body",
            )

        # Store first event (envelopes typically carry one event)
        event = events[0]
        event_id, is_duplicate = sentry_compat.store_event(event)

        return {
            "event_id": event_id,
            "duplicate": is_duplicate,
            "stack_hash": event.stack_hash,
        }

    # ------------------------------------------------------------------
    # Tester invite endpoints
    # ------------------------------------------------------------------
    app.include_router(tester_invite.router)
    app.include_router(appcast_server.router)

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

    uvicorn.run(create_app(), host=args.host, port=args.port)


# ---------------------------------------------------------------------------
# Module-level app instance (for tests that import directly)
# ---------------------------------------------------------------------------
app = create_app()
