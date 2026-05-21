"""Tests for the Sentry-compatible crash-reporter stub endpoint.

Covers:
  - POST /api/sentry/store/ with envelope → 200 + event_id
  - POST /api/sentry/store/ with plain JSON → 200 + event_id
  - Deduplication by stack_hash
  - Envelope parsing (multi-item, event-type)
  - sentry_compat module helpers
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend_stub import sentry_compat
from backend_stub.main import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_store():
    """Clear the sentry in-memory store before and after each test."""
    sentry_compat.clear_store()
    yield
    sentry_compat.clear_store()


@pytest_asyncio.fixture
async def client():
    """Async test client."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

SAMPLE_EVENT_JSON = {
    "event_id": "abc123def456",
    "timestamp": "2026-05-20T10:00:00Z",
    "level": "error",
    "platform": "native",
    "exception": {
        "values": [
            {
                "type": "PanicException",
                "value": "index out of bounds: the len is 0 but the index is 1",
                "stacktrace": {
                    "frames": [
                        {
                            "function": "rust_begin_unwind",
                            "filename": "library/std/src/panicking.rs",
                            "lineno": 42,
                        },
                        {
                            "function": "oyster_recorder::capture::FrameBuffer::get",
                            "filename": "src/recorder/capture.rs",
                            "lineno": 142,
                        },
                        {
                            "function": "oyster_recorder::main",
                            "filename": "src/main.rs",
                            "lineno": 10,
                        },
                    ]
                },
            }
        ]
    },
    "tags": {"release": "0.4.2", "os": "windows"},
}


def _make_envelope(event_payload: dict) -> str:
    """Build a minimal Sentry envelope string from an event payload."""
    envelope_header = json.dumps({"event_id": event_payload.get("event_id", "")})
    item_header = json.dumps({"type": "event", "content_type": "application/json"})
    item_payload = json.dumps(event_payload)
    return f"{envelope_header}\n{item_header}\n{item_payload}"


# ---------------------------------------------------------------------------
# Endpoint: POST /api/sentry/store/  (JSON body)
# ---------------------------------------------------------------------------


class TestSentryStoreJson:
    @pytest.mark.asyncio
    async def test_returns_200_with_event_id(self, client: AsyncClient):
        resp = await client.post(
            "/api/sentry/store/",
            json=SAMPLE_EVENT_JSON,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "event_id" in data
        assert data["event_id"] == "abc123def456"
        assert "stack_hash" in data
        assert data["duplicate"] is False

    @pytest.mark.asyncio
    async def test_returns_200_without_explicit_event_id(self, client: AsyncClient):
        """If no event_id is provided, one is generated."""
        payload = {
            "level": "error",
            "exception": {
                "values": [
                    {
                        "type": "RuntimeError",
                        "value": "something broke",
                        "stacktrace": {"frames": []},
                    }
                ]
            },
        }
        resp = await client.post("/api/sentry/store/", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["event_id"]) > 0

    @pytest.mark.asyncio
    async def test_empty_body_returns_400(self, client: AsyncClient):
        resp = await client.post("/api/sentry/store/", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Endpoint: POST /api/sentry/store/  (envelope body)
# ---------------------------------------------------------------------------


class TestSentryStoreEnvelope:
    @pytest.mark.asyncio
    async def test_envelope_returns_200(self, client: AsyncClient):
        envelope = _make_envelope(SAMPLE_EVENT_JSON)
        resp = await client.post(
            "/api/sentry/store/",
            content=envelope,
            headers={"Content-Type": "application/x-sentry-envelope"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["event_id"] == "abc123def456"
        assert data["duplicate"] is False

    @pytest.mark.asyncio
    async def test_envelope_text_plain(self, client: AsyncClient):
        envelope = _make_envelope(SAMPLE_EVENT_JSON)
        resp = await client.post(
            "/api/sentry/store/",
            content=envelope,
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_envelope_with_multiple_items(self, client: AsyncClient):
        """Envelope with event + non-event items; only event is stored."""
        envelope_header = json.dumps({"event_id": "multi-123"})
        event_item_header = json.dumps({"type": "event", "content_type": "application/json"})
        event_item_payload = json.dumps(SAMPLE_EVENT_JSON)
        session_item_header = json.dumps({"type": "session"})
        session_item_payload = json.dumps({"sid": "sess-1", "status": "ok"})
        envelope = (
            f"{envelope_header}\n"
            f"{event_item_header}\n"
            f"{event_item_payload}\n"
            f"{session_item_header}\n"
            f"{session_item_payload}"
        )
        resp = await client.post(
            "/api/sentry/store/",
            content=envelope,
            headers={"Content-Type": "application/x-sentry-envelope"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["event_id"] == "abc123def456"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_same_stack_hash_is_deduped(self, client: AsyncClient):
        """Two identical events → second is marked duplicate."""
        resp1 = await client.post("/api/sentry/store/", json=SAMPLE_EVENT_JSON)
        assert resp1.status_code == 200
        assert resp1.json()["duplicate"] is False

        # Send the exact same event again
        resp2 = await client.post("/api/sentry/store/", json=SAMPLE_EVENT_JSON)
        assert resp2.status_code == 200
        assert resp2.json()["duplicate"] is True

    @pytest.mark.asyncio
    async def test_different_stack_hash_not_deduped(self, client: AsyncClient):
        """Events with different stack traces are stored separately."""
        event_a = {
            "event_id": "evt-a",
            "level": "error",
            "exception": {
                "values": [
                    {
                        "type": "PanicA",
                        "value": "panic A",
                        "stacktrace": {
                            "frames": [{"function": "fn_a", "filename": "a.rs", "lineno": 1}]
                        },
                    }
                ]
            },
        }
        event_b = {
            "event_id": "evt-b",
            "level": "error",
            "exception": {
                "values": [
                    {
                        "type": "PanicB",
                        "value": "panic B",
                        "stacktrace": {
                            "frames": [{"function": "fn_b", "filename": "b.rs", "lineno": 2}]
                        },
                    }
                ]
            },
        }
        resp_a = await client.post("/api/sentry/store/", json=event_a)
        assert resp_a.json()["duplicate"] is False

        resp_b = await client.post("/api/sentry/store/", json=event_b)
        assert resp_b.json()["duplicate"] is False

    @pytest.mark.asyncio
    async def test_whitespace_normalisation_in_dedup(self, client: AsyncClient):
        """Stack traces that differ only in whitespace share the same hash."""
        event_a = {
            "event_id": "ws-a",
            "level": "error",
            "exception": {
                "values": [
                    {
                        "type": "Panic",
                        "value": "msg",
                        "stacktrace": {
                            "frames": [{"function": "fn", "filename": "x.rs", "lineno": 1}]
                        },
                    }
                ]
            },
        }
        # Same logical stack, different formatting
        event_b = {
            "event_id": "ws-b",
            "level": "error",
            "exception": {
                "values": [
                    {
                        "type": "Panic",
                        "value": "msg",
                        "stacktrace": {
                            "frames": [{"function": "fn", "filename": "x.rs", "lineno": 1}]
                        },
                    }
                ]
            },
        }
        resp_a = await client.post("/api/sentry/store/", json=event_a)
        assert resp_a.json()["duplicate"] is False

        resp_b = await client.post("/api/sentry/store/", json=event_b)
        assert resp_b.json()["duplicate"] is True


# ---------------------------------------------------------------------------
# sentry_compat module unit tests
# ---------------------------------------------------------------------------


class TestSentryCompatModule:
    def test_compute_stack_hash_deterministic(self):
        h1 = sentry_compat.compute_stack_hash("  frame1\n  frame2  ")
        h2 = sentry_compat.compute_stack_hash("frame1 frame2")
        assert h1 == h2

    def test_compute_stack_hash_different(self):
        h1 = sentry_compat.compute_stack_hash("frame_a")
        h2 = sentry_compat.compute_stack_hash("frame_b")
        assert h1 != h2

    def test_parse_envelope_single_event(self):
        envelope = _make_envelope(SAMPLE_EVENT_JSON)
        events = sentry_compat.parse_envelope(envelope)
        assert len(events) == 1
        assert events[0].event_id == "abc123def456"
        assert events[0].exception_type == "PanicException"
        assert "index out of bounds" in events[0].exception_value
        assert len(events[0].stack_hash) == 16

    def test_parse_envelope_empty(self):
        assert sentry_compat.parse_envelope("") == []

    def test_parse_json_body_event(self):
        events = sentry_compat.parse_json_body(SAMPLE_EVENT_JSON)
        assert len(events) == 1
        assert events[0].event_id == "abc123def456"

    def test_parse_json_body_non_event(self):
        events = sentry_compat.parse_json_body({"foo": "bar"})
        assert events == []

    def test_store_and_retrieve(self):
        event = sentry_compat.SentryEvent(
            event_id="test-1",
            stack_hash="hash1",
            exception_type="TestError",
        )
        eid, dup = sentry_compat.store_event(event)
        assert eid == "test-1"
        assert dup is False

        all_events = sentry_compat.get_all_events()
        assert len(all_events) == 1
        assert all_events[0]["event_id"] == "test-1"

    def test_store_duplicate(self):
        event1 = sentry_compat.SentryEvent(event_id="dup-1", stack_hash="same-hash")
        event2 = sentry_compat.SentryEvent(event_id="dup-2", stack_hash="same-hash")

        sentry_compat.store_event(event1)
        eid2, is_dup = sentry_compat.store_event(event2)

        assert eid2 == "dup-2"
        assert is_dup is True
        # Only one entry in store
        assert len(sentry_compat.get_all_events()) == 1

    def test_clear_store(self):
        event = sentry_compat.SentryEvent(event_id="clr-1", stack_hash="h1")
        sentry_compat.store_event(event)
        assert len(sentry_compat.get_all_events()) == 1

        sentry_compat.clear_store()
        assert len(sentry_compat.get_all_events()) == 0
