#!/usr/bin/env python3
"""
bin/backend_stub.py — Minimal FastAPI backend stub for load testing.

Simulates the S25 backend ingest endpoint. Accepts session uploads,
returns 200 with a session_id. Used by load_test_100_recorders.py.

Usage
-----
    python3 bin/backend_stub.py --port 8500
"""

from __future__ import annotations

import argparse
import logging
import time
import uuid

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [stub] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Backend Stub (S25)")

# In-memory store for uploaded sessions
_sessions: list[dict] = []


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "sessions_received": len(_sessions)}


@app.post("/v1/session/upload")
async def upload_session(
    session_data: UploadFile = File(...),
    recorder_id: str = Form(default="unknown"),
) -> JSONResponse:
    """
    Accept a session upload from a recorder.

    Simulates backend processing with a small delay (5-20ms).
    """
    start = time.monotonic()

    # Read the uploaded data (simulate processing)
    data = await session_data.read()
    size = len(data)

    # Simulate small processing delay
    time.sleep(0.005 + (size / 1_000_000) * 0.01)

    session_id = str(uuid.uuid4())
    elapsed_ms = (time.monotonic() - start) * 1000

    record = {
        "session_id": session_id,
        "recorder_id": recorder_id,
        "size_bytes": size,
        "processing_ms": round(elapsed_ms, 2),
    }
    _sessions.append(record)

    logger.info(
        "Session %s from recorder=%s size=%d bytes in %.1fms",
        session_id,
        recorder_id,
        size,
        elapsed_ms,
    )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "session_id": session_id,
            "recorder_id": recorder_id,
            "processing_ms": round(elapsed_ms, 2),
        },
    )


@app.get("/v1/sessions")
async def list_sessions() -> dict:
    """List all received sessions."""
    return {"count": len(_sessions), "sessions": _sessions}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backend stub server")
    parser.add_argument("--port", type=int, default=8500, help="Port to bind")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    args = parser.parse_args()

    import uvicorn

    logger.info("Starting backend stub on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
