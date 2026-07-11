"""
backend_stub.main – local FastAPI server for recorder integration testing.

Endpoints:
  POST /api/v1/auth/google/exchange   → mock OAuth exchange
  POST /api/v1/auth/discord/exchange  → mock OAuth exchange
  GET  /api/v1/income/today           → today's income summary
  POST /api/v1/upload/signed-url      → local presigned upload URL
  PUT  /api/v1/upload/object/{key}    → in-memory upload target
  POST /api/v1/sessions               → register a session
  POST /api/v1/testers/apply          → apply for beta access
  GET  /api/v1/testers                → list all applicants (admin)
  POST /api/v1/testers/{id}/approve   → approve + return signed download URL
  POST /api/v1/testers/{id}/reject    → reject application
  GET  /api/v1/admin/state            → non-PII backend state summary
  POST /api/sentry/store/             → accept Sentry-format crash envelopes

Data lives in memory by default and can be persisted to a local JSON state
file via OYSTER_BACKEND_STATE_FILE. No DB, no external services.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from backend_stub import appcast_server, crash_dump, sentry_compat, tester_invite
from backend_stub.income_engine import calculate_daily_income
from backend_stub.payout import PayoutStore, PayoutWorker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
_income_store: Dict[str, Any] = {}
_sessions_store: Dict[str, Any] = {}
_uploads_store: Dict[str, Any] = {}
_telemetry_store: list[dict[str, Any]] = []
store = PayoutStore()
ADMIN_TOKEN = "admin-secret-token"
_state_file: Path | None = None

STUB_PROVIDER_VALUES = {
    "",
    "dev",
    "demo",
    "fake",
    "local",
    "mock",
    "none",
    "sim",
    "simulator",
    "stub",
    "test",
}

_TELEMETRY_FIELDS = {
    "anon_id": str,
    "version": str,
    "os": str,
    "sessions_today": int,
    "uploads_today": int,
    "total_session_seconds": int,
    "crash_today": bool,
    "ts": str,
}


def _today_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _normalise_provider(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _backend_mode() -> str:
    return os.getenv("OYSTER_BACKEND_MODE", "internal").strip().lower() or "internal"


def _provider_config() -> dict[str, str]:
    return {
        "oauth": os.getenv("GAMEDATA_OAUTH_PROVIDER", "mock").strip() or "mock",
        "storage": os.getenv("GAMEDATA_STORAGE_PROVIDER", "local").strip() or "local",
        "payout": os.getenv("GAMEDATA_PAYOUT_PROVIDER", "simulator").strip() or "simulator",
    }


def _validate_production_backend_config() -> None:
    if _backend_mode() != "production":
        return

    provider_config = _provider_config()
    stubbed = [
        name
        for name, value in provider_config.items()
        if _normalise_provider(value) in STUB_PROVIDER_VALUES
    ]
    if not stubbed:
        return

    details = ", ".join(f"{name}={provider_config[name]}" for name in stubbed)
    raise RuntimeError(
        "OYSTER_BACKEND_MODE=production requires real providers; "
        f"stub provider config is not allowed: {details}"
    )


def _state_file_from_env() -> Path | None:
    raw_path = os.getenv("OYSTER_BACKEND_STATE_FILE", "").strip()
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def _state_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "saved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "income": _income_store,
        "sessions": _sessions_store,
        "uploads": _uploads_store,
        "telemetry": _telemetry_store,
        "testers": tester_invite.get_store().to_dicts(),
    }


def _restore_state(payload: dict[str, Any]) -> None:
    _income_store.clear()
    _sessions_store.clear()
    _uploads_store.clear()
    _telemetry_store.clear()

    income = payload.get("income", {})
    sessions = payload.get("sessions", {})
    uploads = payload.get("uploads", {})
    telemetry = payload.get("telemetry", [])
    testers = payload.get("testers", [])

    if isinstance(income, dict):
        _income_store.update(income)
    if isinstance(sessions, dict):
        _sessions_store.update(sessions)
    if isinstance(uploads, dict):
        _uploads_store.update(uploads)
    if isinstance(telemetry, list):
        _telemetry_store.extend(item for item in telemetry if isinstance(item, dict))
    if isinstance(testers, list):
        tester_invite.get_store().replace_all([item for item in testers if isinstance(item, dict)])


def _load_state_from_disk(path: Path) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid backend state file JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid backend state file payload: {path}")
    _restore_state(payload)


def _persist_state() -> None:
    if _state_file is None:
        return

    _state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(_state_file.parent),
            prefix=f".{_state_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name
            json.dump(_state_payload(), tmp, ensure_ascii=False, sort_keys=True)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, _state_file)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _configure_persistence() -> None:
    global _state_file
    _state_file = _state_file_from_env()
    tester_invite.get_store().set_change_hook(_persist_state if _state_file else None)
    if _state_file is not None:
        _load_state_from_disk(_state_file)


def _persist_after_write() -> None:
    _persist_state()


def _session_income_status(body: dict[str, Any]) -> str:
    status = body.get("status")
    if isinstance(status, str) and status:
        return status
    return "FAIL"


def _recalculate_income(today: str) -> dict[str, Any]:
    sessions_today = [
        {
            "status": session.get("income_status", "FAIL"),
            "date": session.get("date", today),
        }
        for session in _sessions_store.values()
        if session.get("date") == today
    ]
    income = calculate_daily_income(sessions_today)
    if income["date"] == "unknown":
        income["date"] = today
    _income_store[today] = income
    return income


def _validate_telemetry_payload(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Telemetry payload must be an object")

    missing = sorted(set(_TELEMETRY_FIELDS) - set(body))
    extra = sorted(set(body) - set(_TELEMETRY_FIELDS))
    if missing or extra:
        raise HTTPException(
            status_code=400,
            detail={"missing": missing, "extra": extra},
        )

    record: dict[str, Any] = {}
    for key, expected_type in _TELEMETRY_FIELDS.items():
        value = body[key]
        if expected_type is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise HTTPException(status_code=400, detail=f"{key} must be an integer")
        elif not isinstance(value, expected_type):
            raise HTTPException(status_code=400, detail=f"{key} has invalid type")
        record[key] = value

    try:
        _dt.datetime.fromisoformat(record["ts"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ts must be ISO-8601") from exc

    return record


def _empty_income_summary(today: str) -> dict[str, Any]:
    return {
        "date": today,
        "total_usd": 0.0,
        "sessions_uploaded": 0,
        "sessions_counted": 0,
        "currency": "USD",
    }


def _admin_state_summary() -> dict[str, Any]:
    today = _today_iso()
    testers = tester_invite.get_store().list_all()
    tester_statuses = dict.fromkeys(sorted(tester_invite.VALID_STATUSES), 0)
    for tester in testers:
        tester_statuses[tester.status] = tester_statuses.get(tester.status, 0) + 1

    sessions_today = sum(1 for session in _sessions_store.values() if session.get("date") == today)
    uploads_today = sum(
        1
        for upload in _uploads_store.values()
        if str(upload.get("uploaded_at", "")).startswith(today)
    )

    return {
        "status": "ok",
        "date": today,
        "persistence": "enabled" if _state_file is not None else "memory",
        "counts": {
            "income_days": len(_income_store),
            "sessions": len(_sessions_store),
            "sessions_today": sessions_today,
            "uploads": len(_uploads_store),
            "uploads_today": uploads_today,
            "telemetry_events": len(_telemetry_store),
            "testers": len(testers),
            "tester_statuses": tester_statuses,
        },
        "income_today": _income_store.get(today, _empty_income_summary(today)),
        "recorder_release": {
            "tag": os.getenv("OYSTER_RECORDER_RELEASE_TAG", ""),
            "version": os.getenv("OYSTER_RECORDER_RELEASE_VERSION", ""),
        },
    }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _gcs_signed_put_url(bucket_name: str, key: str) -> str:
    """V4 signed PUT URL so recorder clips stream straight to GCS.

    Uploads must NOT pass through this service (256Mi container, large
    clips). On Cloud Run there is no key file, so sign via IAM signBlob
    using the runtime service account (needs
    roles/iam.serviceAccountTokenCreator on itself).
    """
    import datetime as dt

    from google.cloud import storage  # lazy: only imported in bucket mode

    client = storage.Client()
    blob = client.bucket(bucket_name).blob(key)
    kwargs: dict = {
        "version": "v4",
        "expiration": dt.timedelta(hours=1),
        "method": "PUT",
    }
    try:
        from google.auth import default as ga_default
        from google.auth.transport import requests as ga_requests

        creds, _ = ga_default()
        if not getattr(creds, "private_key", None) and hasattr(creds, "service_account_email"):
            creds.refresh(ga_requests.Request())
            kwargs["service_account_email"] = creds.service_account_email
            kwargs["access_token"] = creds.token
    except Exception as exc:
        # local dev: key file / emulator can sign without signBlob.
        # Surface the error at DEBUG so operators can diagnose signBlob
        # auth issues without changing the public contract (still falls
        # back to generate_signed_url() with kwargs as-is).
        logger.debug("GCS signed-url creds enrichment skipped: %s", exc)
    return blob.generate_signed_url(**kwargs)


def create_app(accelerate: float = 1.0, interval: float = 300.0) -> FastAPI:
    _configure_persistence()
    _validate_production_backend_config()
    app = FastAPI(title="gamedata-pipeline backend stub", version="0.1.0")
    app.state.payout_accelerate = accelerate
    app.state.payout_interval = interval
    app.state.payout_routes_registered = False
    app.state.payout_worker = None

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
    @app.get("/api/v1/healthz")
    async def healthz():
        return {
            "status": "ok",
            "version": "0.1.0",
            "persistence": "enabled" if _state_file is not None else "memory",
            "mode": _backend_mode(),
            "providers": _provider_config(),
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/admin/state")
    async def admin_state(authorization: str | None = Header(default=None)):
        tester_invite._require_admin(authorization)
        return _admin_state_summary()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_bearer(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")
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
        today = _today_iso()
        if today not in _income_store:
            _income_store[today] = {
                "date": today,
                "total_usd": 0.0,
                "sessions_uploaded": 0,
                "sessions_counted": 0,
                "currency": "USD",
            }
        return _income_store[today]

    # ------------------------------------------------------------------
    # Upload signed URL (local in-memory target)
    # ------------------------------------------------------------------
    @app.post("/api/v1/upload/signed-url")
    async def upload_signed_url(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        _require_bearer(authorization)
        body = await request.json()
        key = body.get("key", f"uploads/{uuid.uuid4().hex}.bin")
        expires_at = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)).isoformat()
        bucket_name = os.getenv("OYSTER_GCS_BUCKET")
        if bucket_name:
            return {
                "url": _gcs_signed_put_url(bucket_name, str(key)),
                "expires_at": expires_at,
                "key": key,
            }
        escaped_key = quote(str(key), safe="/")
        return {
            "url": f"{str(request.base_url).rstrip('/')}/api/v1/upload/object/{escaped_key}",
            "expires_at": expires_at,
            "key": key,
        }

    @app.put("/api/v1/upload/object/{key:path}")
    async def upload_object(key: str, request: Request):
        payload = await request.body()
        if not payload:
            raise HTTPException(status_code=400, detail="Upload body must not be empty")
        _uploads_store[key] = {
            "key": key,
            "size": len(payload),
            "content_type": request.headers.get("content-type"),
            "uploaded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        _persist_after_write()
        return Response(status_code=200)

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
        today = _today_iso()
        income_status = _session_income_status(body)
        _sessions_store[session_id] = {
            "session_id": session_id,
            "status": "received",
            "income_status": income_status,
            "date": today,
            "upload_key": body.get("upload_key"),
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        income = _recalculate_income(today)
        _persist_after_write()
        return {
            "session_id": session_id,
            "status": "received",
            "income_status": income_status,
            "income_today": income,
        }

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
    # Crash dump endpoints
    # ------------------------------------------------------------------
    @app.post("/api/v1/crash/dump")
    async def post_crash_dump(request: Request):
        body = await request.json()
        dump = crash_dump.CrashDump(
            panic_message=str(body.get("panic_message", "")),
            stack_trace=str(body.get("stack_trace", "")),
            os_info=str(body.get("os_info", "")),
            recorder_version=str(body.get("recorder_version", "")),
            raw_file=str(body.get("raw_file", "")),
        )
        crash_id = crash_dump.store_crash(dump)
        return {"status": "accepted", "id": crash_id}

    @app.get("/api/v1/crash/dump")
    async def list_crash_dumps():
        return crash_dump.get_all_crashes()

    @app.delete("/api/v1/crash/dump")
    async def clear_crash_dumps():
        crash_dump.clear_crashes()
        return {"status": "cleared"}

    # ------------------------------------------------------------------
    # Anonymous telemetry endpoint
    # ------------------------------------------------------------------
    @app.post("/api/v1/telemetry/daily")
    async def telemetry_daily(request: Request):
        body = await request.json()
        record = _validate_telemetry_payload(body)
        _telemetry_store.append(record)
        _persist_after_write()
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Tester invite endpoints
    # ------------------------------------------------------------------
    app.include_router(tester_invite.router)
    app.include_router(appcast_server.router)

    return app


def register_routes(app: FastAPI) -> FastAPI:
    """Register payout simulator routes on an app instance."""
    if getattr(app.state, "payout_routes_registered", False):
        return app
    app.state.payout_routes_registered = True

    def _require_token(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")
        return authorization[7:]

    def _require_admin(authorization: str | None) -> None:
        if _require_token(authorization) != ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Admin token required")

    async def _json_body(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON")
        return body

    @app.post("/api/v1/payouts/queue")
    async def queue_payout(request: Request, authorization: str | None = Header(default=None)):
        user_id = _require_token(authorization)
        body = await _json_body(request)
        amount = body.get("amount_usd")
        provider = body.get("provider", "paypal")
        if not isinstance(amount, (int, float)) or amount <= 0:
            raise HTTPException(status_code=400, detail="amount_usd must be positive")
        if provider not in {"paypal", "stripe"}:
            raise HTTPException(status_code=400, detail="Unsupported provider")
        if not store.can_payout(user_id, float(amount)):
            return JSONResponse(
                status_code=429,
                content={
                    "error": "daily_limit_exceeded",
                    "retry_after": 86400,
                },
                headers={"Retry-After": "86400"},
            )
        record = store.create(user_id, float(amount), provider=provider)
        store.record_daily(user_id, float(amount))
        data = record.to_dict()
        data["payout_id"] = record.id
        return data

    @app.get("/api/v1/payouts/{payout_id}")
    async def get_payout(payout_id: str, authorization: str | None = Header(default=None)):
        _require_token(authorization)
        record = store.get(payout_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Payout not found")
        return record.to_dict()

    @app.post("/api/v1/payouts/{payout_id}/simulate")
    async def simulate_paid(payout_id: str, authorization: str | None = Header(default=None)):
        _require_admin(authorization)
        record = store.force_paid(payout_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Payout not found")
        return record.to_dict()

    @app.post("/api/v1/payouts/{payout_id}/simulate-fail")
    async def simulate_failed(
        payout_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        _require_admin(authorization)
        body = await _json_body(request)
        record = store.force_failed(payout_id, body.get("reason", "mock_failure"))
        if record is None:
            raise HTTPException(status_code=404, detail="Payout not found")
        return record.to_dict()

    @app.get("/api/v1/payouts")
    async def list_payouts(authorization: str | None = Header(default=None)):
        _require_admin(authorization)
        return [record.to_dict() for record in store.list_all()]

    accelerate = float(getattr(app.state, "payout_accelerate", 1.0))
    interval = float(getattr(app.state, "payout_interval", 300.0))
    if accelerate > 1.0 or interval < 300.0:

        @app.on_event("startup")
        async def _start_payout_worker():
            if app.state.payout_worker is None:
                app.state.payout_worker = PayoutWorker(
                    store, accelerate=accelerate, interval=interval
                )
                app.state.payout_worker.start()

        @app.on_event("shutdown")
        async def _stop_payout_worker():
            if app.state.payout_worker is not None:
                app.state.payout_worker.stop()
                app.state.payout_worker = None

    return app


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="gamedata-pipeline backend stub")
    parser.add_argument("--port", type=int, default=8500, help="Port to listen on (default: 8500)")
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
