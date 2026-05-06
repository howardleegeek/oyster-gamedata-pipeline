#!/usr/bin/env python3
"""
epal_session_lifecycle_hook.py — EPal Session Lifecycle Webhook Handler
=======================================================================

Handles POST /v1/epal/session_start and /v1/epal/session_end webhooks.

- session_start: starts recorder ONLY when the session is paid AND the
  companion has opted in.
- session_end: stops the recorder, attaches rating + companion_id
  provenance, and uploads the session artefact.

Usage (CLI):
    python3 bin/epal_session_lifecycle_hook.py session-start \
        --session-id <id> --companion-id <id> --is-paid true --opt-in true
    python3 bin/epal_session_lifecycle_hook.py session-end \
        --session-id <id> --rating 5 --companion-id <id>
    python3 bin/epal_session_lifecycle_hook.py serve --port 8080
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("epal_lifecycle")

DEFAULT_UPLOAD_DIR: str = os.environ.get("EPAL_UPLOAD_DIR", "")
DEFAULT_STATE_DIR: str = os.environ.get("EPAL_STATE_DIR", "")
WEBHOOK_SECRET_ENV: str = "EPAL_WEBHOOK_SECRET"

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SessionContext:
    """Mutable state for an active EPal session."""
    session_id: str
    companion_id: str
    is_paid: bool
    companion_opt_in: bool
    started_at: str = ""
    recorder_pid: Optional[int] = None
    recording_path: Optional[str] = None
    temp_dir: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionContext":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SessionEndPayload:
    """Payload produced when a session ends."""
    session_id: str
    companion_id: str
    rating: int
    ended_at: str
    duration_seconds: float
    recording_path: str
    checksum_sha256: str
    provenance: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Session store (in-memory; swap for Redis/DB in production)
# ---------------------------------------------------------------------------
_active_sessions: Dict[str, SessionContext] = {}


def _state_file(session_id: str) -> Path:
    """Return the path to the persisted state file for a session."""
    base = Path(DEFAULT_STATE_DIR) if DEFAULT_STATE_DIR else Path(tempfile.mkdtemp(prefix="epal_state_"))
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{session_id}.json"


def _persist_session(ctx: SessionContext) -> None:
    """Persist session context to disk for crash recovery."""
    _state_file(ctx.session_id).write_text(json.dumps(ctx.to_dict(), indent=2))


def _load_session(session_id: str) -> Optional[SessionContext]:
    """Load a persisted session context, if available."""
    path = _state_file(session_id)
    if path.exists():
        return SessionContext.from_dict(json.loads(path.read_text()))
    return None


# ---------------------------------------------------------------------------
# Recorder helpers
# ---------------------------------------------------------------------------

def _start_recorder(session_id: str, temp_dir: str) -> tuple[int, str]:
    """Start the recording process. Returns (pid, recording_path)."""
    rec_path = os.path.join(temp_dir, f"{session_id}.rec")
    Path(rec_path).write_text(f"RECORDER_START {session_id} {datetime.now(timezone.utc).isoformat()}\n")
    pid = os.getpid()  # In production: subprocess.Popen(RECORDER_CMD, ...).pid
    logger.info("Recorder started: pid=%s path=%s", pid, rec_path)
    return pid, rec_path


def _stop_recorder(pid: int, recording_path: str) -> None:
    """Stop the recording process and finalise the recording file."""
    if recording_path and Path(recording_path).exists():
        with open(recording_path, "a") as fh:
            fh.write(f"RECORDER_STOP {datetime.now(timezone.utc).isoformat()}\n")
    logger.info("Recorder stopped: pid=%s path=%s", pid, recording_path)


def _compute_checksum(path: str) -> str:
    """Compute SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Upload helper
# ---------------------------------------------------------------------------

def _upload_artefact(payload: SessionEndPayload) -> str:
    """Upload the session artefact. Returns the destination path."""
    upload_base = Path(DEFAULT_UPLOAD_DIR) if DEFAULT_UPLOAD_DIR else Path(tempfile.mkdtemp(prefix="epal_upload_"))
    upload_base.mkdir(parents=True, exist_ok=True)
    dest = upload_base / f"{payload.session_id}_end.json"
    dest.write_text(json.dumps(payload.to_dict(), indent=2))
    logger.info("Artefact uploaded to %s", dest)
    return str(dest)


# ---------------------------------------------------------------------------
# Core webhook handlers
# ---------------------------------------------------------------------------

def handle_session_start(
    session_id: str, companion_id: str, is_paid: bool, companion_opt_in: bool,
) -> Dict[str, Any]:
    """
    Process a session_start webhook.

    Recorder starts ONLY when is_paid AND companion_opt_in are both True.
    """
    logger.info("session_start: id=%s companion=%s paid=%s opt_in=%s",
                session_id, companion_id, is_paid, companion_opt_in)

    if not is_paid:
        return {"status": "skipped", "reason": "session_not_paid", "session_id": session_id}
    if not companion_opt_in:
        return {"status": "skipped", "reason": "companion_opt_out", "session_id": session_id}

    temp_dir = tempfile.mkdtemp(prefix=f"epal_{session_id}_")
    pid, rec_path = _start_recorder(session_id, temp_dir)

    ctx = SessionContext(
        session_id=session_id, companion_id=companion_id,
        is_paid=True, companion_opt_in=True,
        started_at=datetime.now(timezone.utc).isoformat(),
        recorder_pid=pid, recording_path=rec_path, temp_dir=temp_dir,
    )
    _active_sessions[session_id] = ctx
    _persist_session(ctx)

    return {"status": "recording_started", "session_id": session_id,
            "recorder_pid": pid, "recording_path": rec_path}


def handle_session_end(
    session_id: str, companion_id: str, rating: int,
) -> Dict[str, Any]:
    """
    Process a session_end webhook.

    Stops recorder, computes provenance, uploads artefact.
    """
    logger.info("session_end: id=%s companion=%s rating=%d", session_id, companion_id, rating)

    ctx = _active_sessions.get(session_id) or _load_session(session_id)
    if ctx is None:
        return {"status": "error", "reason": "session_not_found", "session_id": session_id}

    if ctx.companion_id != companion_id:
        return {"status": "error", "reason": "companion_id_mismatch",
                "expected": ctx.companion_id, "received": companion_id}

    if ctx.recorder_pid is not None and ctx.recording_path:
        _stop_recorder(ctx.recorder_pid, ctx.recording_path)

    started = datetime.fromisoformat(ctx.started_at) if ctx.started_at else datetime.now(timezone.utc)
    ended = datetime.now(timezone.utc)
    duration = (ended - started).total_seconds()

    rec_path = ctx.recording_path or ""
    checksum = _compute_checksum(rec_path) if rec_path and Path(rec_path).exists() else ""

    provenance = {
        "companion_id": companion_id, "session_id": session_id,
        "is_paid": str(ctx.is_paid), "companion_opt_in": str(ctx.companion_opt_in),
        "started_at": ctx.started_at, "ended_at": ended.isoformat(),
    }

    payload = SessionEndPayload(
        session_id=session_id, companion_id=companion_id, rating=rating,
        ended_at=ended.isoformat(), duration_seconds=duration,
        recording_path=rec_path, checksum_sha256=checksum, provenance=provenance,
    )
    upload_dest = _upload_artefact(payload)
    _active_sessions.pop(session_id, None)

    return {"status": "session_ended", "session_id": session_id,
            "upload_dest": upload_dest, "duration_seconds": duration,
            "checksum_sha256": checksum}


# ---------------------------------------------------------------------------
# HTTP webhook server
# ---------------------------------------------------------------------------

class WebhookHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for EPal lifecycle webhooks."""

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _send_json(self, code: int, data: Dict[str, Any]) -> None:
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _verify_secret(self) -> bool:
        secret = os.environ.get(WEBHOOK_SECRET_ENV, "")
        if not secret:
            return True
        return self.headers.get("X-EPAL-Secret", "") == secret

    def do_POST(self) -> None:  # noqa: N802
        if not self._verify_secret():
            self._send_json(401, {"error": "unauthorized"})
            return

        body = self._read_body()
        if self.path == "/v1/epal/session_start":
            result = handle_session_start(
                body.get("session_id", ""), body.get("companion_id", ""),
                bool(body.get("is_paid", False)), bool(body.get("companion_opt_in", False)),
            )
        elif self.path == "/v1/epal/session_end":
            result = handle_session_end(
                body.get("session_id", ""), body.get("companion_id", ""),
                int(body.get("rating", 0)),
            )
        else:
            self._send_json(404, {"error": "not_found"})
            return

        self._send_json(200 if result["status"] != "error" else 400, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info(fmt, *args)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EPal Session Lifecycle Webhook Handler")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("session-start")
    p.add_argument("--session-id", required=True)
    p.add_argument("--companion-id", required=True)
    p.add_argument("--is-paid", type=lambda v: v.lower() == "true", default=False)
    p.add_argument("--opt-in", type=lambda v: v.lower() == "true", default=False)

    p = sub.add_parser("session-end")
    p.add_argument("--session-id", required=True)
    p.add_argument("--companion-id", required=True)
    p.add_argument("--rating", type=int, required=True)

    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the EPal session lifecycle hook CLI."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.command == "session-start":
        result = handle_session_start(args.session_id, args.companion_id,
                                      args.is_paid, args.opt_in)
    elif args.command == "session-end":
        result = handle_session_end(args.session_id, args.companion_id, args.rating)
    elif args.command == "serve":
        server = HTTPServer((args.host, args.port), WebhookHandler)
        logger.info("Serving on %s:%d", args.host, args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
        return 0
    else:
        return 1

    print(json.dumps(result, indent=2))
    return 0 if result["status"] != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
