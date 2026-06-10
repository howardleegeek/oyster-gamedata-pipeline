"""Backend stub for crash dump ingestion.

Provides a minimal FastAPI endpoint that accepts anonymized crash reports
and persists them in memory.  Used by the local crash-reporter daemon
during development / testing.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_crash_store: list[dict[str, Any]] = []


@dataclass
class CrashDump:
    """An anonymized crash report."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    panic_message: str = ""
    stack_trace: str = ""
    os_info: str = ""
    recorder_version: str = ""
    raw_file: str = ""  # original filename (no path)


def store_crash(dump: CrashDump) -> str:
    """Persist a crash dump and return its ID."""
    entry = {
        "id": dump.id,
        "timestamp": dump.timestamp,
        "panic_message": dump.panic_message,
        "stack_trace": dump.stack_trace,
        "os_info": dump.os_info,
        "recorder_version": dump.recorder_version,
        "raw_file": dump.raw_file,
    }
    _crash_store.append(entry)
    return dump.id


def get_all_crashes() -> list[dict[str, Any]]:
    """Return all stored crash dumps."""
    return list(_crash_store)


def clear_crashes() -> None:
    """Clear the in-memory store (useful for tests)."""
    _crash_store.clear()
