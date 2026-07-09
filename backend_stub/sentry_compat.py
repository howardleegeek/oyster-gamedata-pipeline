"""Sentry-compatible crash dump ingestion for backend stub.

Accepts Sentry SDK envelope format (newline-delimited JSON), parses events,
stores them in memory, and deduplicates by stack_hash.

Used by the S51 crash_reporter integration to accept Rust panic_handler
and sentry_log compat crash reports without connecting to a real Sentry
server.  No PII is collected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_sentry_store: List[Dict[str, Any]] = []
_seen_hashes: set[str] = set()


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class SentryEvent:
    """A parsed Sentry crash event."""

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = ""
    level: str = "error"
    platform: str = "native"
    exception_type: str = ""
    exception_value: str = ""
    stack_trace: str = ""
    stack_hash: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compute_stack_hash(stack_trace: str) -> str:
    """Compute a deterministic hash from a stack trace for deduplication.

    Normalises whitespace so that minor formatting differences do not
    produce different hashes.
    """
    normalised = " ".join(stack_trace.split())
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def _parse_event_payload(payload: Dict[str, Any]) -> SentryEvent:
    """Parse a Sentry event JSON payload into a :class:`SentryEvent`."""
    event = SentryEvent(
        event_id=payload.get("event_id", uuid.uuid4().hex),
        timestamp=payload.get("timestamp", ""),
        level=payload.get("level", "error"),
        platform=payload.get("platform", "native"),
        tags=dict(payload.get("tags", {}) or {}),
    )

    # Extract exception info from the Sentry exception chain
    exceptions = (payload.get("exception") or {}).get("values", [])
    if exceptions:
        exc = exceptions[0]
        event.exception_type = exc.get("type", "")
        event.exception_value = exc.get("value", "")

        # Build a human-readable stack trace from frames
        frames = (exc.get("stacktrace") or {}).get("frames", [])
        stack_lines: list[str] = []
        for fr in frames:
            func = fr.get("function", "?")
            filename = fr.get("filename", "?")
            lineno = fr.get("lineno", "?")
            stack_lines.append(f"  at {func} ({filename}:{lineno})")
        event.stack_trace = "\n".join(stack_lines)

    event.stack_hash = compute_stack_hash(event.stack_trace)
    return event


# ---------------------------------------------------------------------------
# Envelope parsing
# ---------------------------------------------------------------------------


def parse_envelope(raw: str) -> List[SentryEvent]:
    """Parse a Sentry envelope (newline-delimited JSON) into events.

    Envelope wire format::

        {envelope_header_json}
        {item_header_json}
        {item_payload_json}
        {item_header_json}
        {item_payload_json}
        ...

    Only ``event``-type items are extracted; other item types (session,
    attachment, …) are silently ignored.
    """
    events: list[SentryEvent] = []
    lines = raw.strip().split("\n")
    if not lines:
        return events

    # Skip envelope header (line 0)
    i = 1
    while i < len(lines):
        try:
            item_header = json.loads(lines[i])
        except (json.JSONDecodeError, IndexError):
            i += 1
            continue
        i += 1

        if i >= len(lines):
            break

        item_type = item_header.get("type", "")

        # Event items have JSON payloads
        if item_type == "event":
            try:
                payload = json.loads(lines[i])
                events.append(_parse_event_payload(payload))
            except json.JSONDecodeError as exc:
                logger.debug(
                    "sentry_compat: skipping malformed event item payload (line %d): %s",
                    i,
                    exc,
                )
        # Exception items may appear as standalone payloads
        elif item_type == "error" or "exception" in (item_header.get("content_type", "")):
            try:
                payload = json.loads(lines[i])
                if "exception" in payload or "level" in payload:
                    events.append(_parse_event_payload(payload))
            except json.JSONDecodeError as exc:
                logger.debug(
                    "sentry_compat: skipping malformed exception item payload (line %d): %s",
                    i,
                    exc,
                )

        i += 1

    return events


def parse_json_body(body: Dict[str, Any]) -> List[SentryEvent]:
    """Parse a plain JSON body (non-envelope) as a single Sentry event.

    Some SDKs / test harnesses send a flat JSON object instead of a full
    envelope.  This helper handles that case.
    """
    if "exception" in body or "level" in body or "message" in body:
        return [_parse_event_payload(body)]
    return []


# ---------------------------------------------------------------------------
# Store operations
# ---------------------------------------------------------------------------


def store_event(event: SentryEvent) -> Tuple[str, bool]:
    """Store a Sentry event.  Returns ``(event_id, is_duplicate)``.

    Deduplication is performed by ``stack_hash`` – if an event with the
    same stack hash has already been stored, it is **not** inserted again
    and ``is_duplicate`` is ``True``.
    """
    if event.stack_hash and event.stack_hash in _seen_hashes:
        return event.event_id, True

    if event.stack_hash:
        _seen_hashes.add(event.stack_hash)

    entry: Dict[str, Any] = {
        "event_id": event.event_id,
        "timestamp": event.timestamp,
        "level": event.level,
        "platform": event.platform,
        "exception_type": event.exception_type,
        "exception_value": event.exception_value,
        "stack_trace": event.stack_trace,
        "stack_hash": event.stack_hash,
        "tags": event.tags,
        "received_at": event.received_at,
    }
    _sentry_store.append(entry)
    return event.event_id, False


def get_all_events() -> List[Dict[str, Any]]:
    """Return all stored Sentry events (shallow copy)."""
    return list(_sentry_store)


def clear_store() -> None:
    """Clear the in-memory store and dedup set (useful for tests)."""
    _sentry_store.clear()
    _seen_hashes.clear()
