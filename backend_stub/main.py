"""Backend stub FastAPI application.

Minimal FastAPI app that exposes the crash-dump ingestion endpoint used by
the local crash-reporter daemon.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from backend_stub.crash_dump import (
    CrashDump,
    clear_crashes,
    get_all_crashes,
    store_crash,
)

app = FastAPI(title="Oyster Backend Stub")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CrashDumpRequest(BaseModel):
    panic_message: str = Field(default="", max_length=4096)
    stack_trace: str = Field(default="", max_length=65536)
    os_info: str = Field(default="", max_length=256)
    recorder_version: str = Field(default="", max_length=64)
    raw_file: str = Field(default="", max_length=512)


class CrashDumpResponse(BaseModel):
    id: str
    status: str = "accepted"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/api/v1/crash/dump", response_model=CrashDumpResponse)
async def post_crash_dump(payload: CrashDumpRequest) -> CrashDumpResponse:
    """Accept an anonymized crash report."""
    dump = CrashDump(
        panic_message=payload.panic_message,
        stack_trace=payload.stack_trace,
        os_info=payload.os_info,
        recorder_version=payload.recorder_version,
        raw_file=payload.raw_file,
    )
    crash_id = store_crash(dump)
    return CrashDumpResponse(id=crash_id)


@app.get("/api/v1/crash/dump", response_model=list[dict])
async def list_crash_dumps() -> list[dict]:
    """List all stored crash dumps (for debugging / testing)."""
    return get_all_crashes()


@app.delete("/api/v1/crash/dump")
async def clear_crash_dumps() -> dict:
    """Clear all stored crash dumps (for testing)."""
    clear_crashes()
    return {"status": "cleared"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
