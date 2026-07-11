#!/usr/bin/env python3
"""backend_stub.py — Minimal FastAPI backend stub for local smoke / CI.

Provides the same HTTP contract that the real ingest backend will expose,
but stores everything in-memory.  Used by ``bin/recorder_local_smoke.py``
so the full upload → verify pipeline can be exercised without a real
cloud service.

Endpoints
---------
    POST /v1/sessions          — Upload a new recording session
    GET  /v1/sessions          — List all sessions
    GET  /v1/sessions/{sid}    — Get a single session by session_id
    GET  /v1/health            — Health check

Usage
-----
    python3 bin/backend_stub.py --port 8500
    # → http://localhost:8500/v1/health  → {"status": "ok"}

Exit codes
----------
    0 — server started (runs until SIGINT/SIGTERM)
    1 — uvicorn not installed or other startup error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# FastAPI imports at module level so type annotations resolve correctly.
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory session store
# ---------------------------------------------------------------------------

_sessions: Dict[str, Dict[str, Any]] = {}


def _reset_store() -> None:
    """Clear all sessions (useful for test teardown)."""
    _sessions.clear()


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and return the FastAPI application."""
    app = FastAPI(title="oyster-backend-stub", version="0.1.0")

    @app.get("/v1/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/sessions")
    async def create_session(
        session_id: str = Form(None),
        game: str = Form(None),
        pid: str = Form(None),
        window_title: str = Form(None),
        device_id: str = Form(None),
        metadata_json: str = Form(None),
        video: UploadFile = File(None),
    ) -> JSONResponse:
        """Accept a new recording session upload."""
        sid = session_id or str(uuid.uuid4())

        # Parse metadata if provided as JSON string
        meta: Dict[str, Any] = {}
        if metadata_json:
            try:
                meta = json.loads(metadata_json)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid metadata_json") from None

        # Override with form fields
        if game:
            meta["game"] = game
        if pid:
            meta["pid"] = int(pid)
        if window_title:
            meta["window_title"] = window_title
        if device_id:
            meta["device_id"] = device_id

        # Read video file size if provided
        video_size = 0
        if video:
            content = await video.read()
            video_size = len(content)

        session_data: Dict[str, Any] = {
            "session_id": sid,
            "metadata": meta,
            "video_size_bytes": video_size,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "status": "received",
        }

        _sessions[sid] = session_data
        logger.info("Session %s received (%d bytes video)", sid, video_size)

        return JSONResponse(
            status_code=201,
            content={
                "session_id": sid,
                "status": "received",
                "video_size_bytes": video_size,
            },
        )

    @app.get("/v1/sessions")
    async def list_sessions() -> List[Dict[str, Any]]:
        """List all received sessions."""
        return list(_sessions.values())

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str) -> Dict[str, Any]:
        """Get a single session by ID."""
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        return _sessions[session_id]

    # Expose reset for testing
    app._reset_store = _reset_store  # type: ignore[attr-defined]
    app._sessions = _sessions  # type: ignore[attr-defined]

    return app


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8500,
        help="Bind port (default: 8500)",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug"],
        help="Log level",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: uvicorn is not installed. "
            "Install with: pip install 'uvicorn[standard]>=0.27'",
            file=sys.stderr,
        )
        return 1

    app = create_app()
    logger.info("Starting backend stub on %s:%d", args.host, args.port)

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
