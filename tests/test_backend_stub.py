"""Tests for backend_stub.main endpoints using httpx.AsyncClient."""

from __future__ import annotations

import datetime as _dt
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend_stub.main import _income_store, _sessions_store, _uploads_store, create_app


@pytest.fixture(autouse=True)
def clear_stores():
    """Clear in-memory stores before each test."""
    _income_store.clear()
    _sessions_store.clear()
    _uploads_store.clear()
    yield
    _income_store.clear()
    _sessions_store.clear()
    _uploads_store.clear()


@pytest_asyncio.fixture
async def client():
    """Async test client."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Auth: Google exchange
# ---------------------------------------------------------------------------
class TestAuthGoogleExchange:
    async def test_returns_tokens(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/google/exchange",
            json={"code": "fake-code", "redirect_uri": "http://localhost/callback"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["expires_in"] == 3600
        assert data["access_token"].startswith("mock-google-at-")

    async def test_no_auth_required(self, client: AsyncClient):
        """Auth exchange endpoints do NOT require Bearer."""
        resp = await client.post(
            "/api/v1/auth/google/exchange",
            json={"code": "x"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth: Discord exchange
# ---------------------------------------------------------------------------
class TestAuthDiscordExchange:
    async def test_returns_tokens(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/discord/exchange",
            json={"code": "fake-code"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["access_token"].startswith("mock-discord-at-")
        assert data["expires_in"] == 3600


# ---------------------------------------------------------------------------
# Income: GET /api/v1/income/today
# ---------------------------------------------------------------------------
class TestIncomeToday:
    async def test_returns_200_with_bearer(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/income/today",
            headers={"Authorization": "Bearer foo"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["currency"] == "USD"
        assert data["total_usd"] == 0.0
        assert data["sessions_uploaded"] == 0
        assert data["date"] == _dt.date.today().isoformat()

    async def test_401_without_bearer(self, client: AsyncClient):
        resp = await client.get("/api/v1/income/today")
        assert resp.status_code == 401

    async def test_401_with_invalid_header(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/income/today",
            headers={"Authorization": "Basic abc"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Upload signed URL: POST /api/v1/upload/signed-url
# ---------------------------------------------------------------------------
class TestUploadSignedUrl:
    async def test_returns_local_upload_url(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/upload/signed-url",
            headers={"Authorization": "Bearer tok"},
            json={"key": "uploads/test.bin"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data
        assert "expires_at" in data
        assert data["key"] == "uploads/test.bin"
        assert data["url"] == "http://test/api/v1/upload/object/uploads/test.bin"

    async def test_signed_url_accepts_put_upload(self, client: AsyncClient):
        signed_resp = await client.post(
            "/api/v1/upload/signed-url",
            headers={"Authorization": "Bearer tok"},
            json={"key": "uploads/test.bin"},
        )
        upload_url = signed_resp.json()["url"]

        put_resp = await client.put(upload_url, content=b"tarball bytes")

        assert put_resp.status_code == 200
        assert _uploads_store["uploads/test.bin"]["size"] == len(b"tarball bytes")

    async def test_upload_rejects_empty_body(self, client: AsyncClient):
        resp = await client.put("/api/v1/upload/object/uploads/empty.bin", content=b"")
        assert resp.status_code == 400

    async def test_generates_key_when_missing(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/upload/signed-url",
            headers={"Authorization": "Bearer tok"},
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "key" in data
        assert data["key"].startswith("uploads/")

    async def test_401_without_bearer(self, client: AsyncClient):
        resp = await client.post("/api/v1/upload/signed-url", json={})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Sessions: POST /api/v1/sessions
# ---------------------------------------------------------------------------
class TestSessions:
    async def test_creates_session(self, client: AsyncClient):
        sid = str(uuid.uuid4())
        resp = await client.post(
            "/api/v1/sessions",
            headers={"Authorization": "Bearer tok"},
            json={"session_id": sid, "status": "BUYER_READY"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid
        assert data["status"] == "received"
        assert data["income_status"] == "BUYER_READY"
        assert data["income_today"]["total_usd"] == 0.50

        income_resp = await client.get(
            "/api/v1/income/today",
            headers={"Authorization": "Bearer tok"},
        )
        assert income_resp.status_code == 200
        income = income_resp.json()
        assert income["total_usd"] == 0.50
        assert income["sessions_uploaded"] == 1
        assert income["sessions_counted"] == 1

    async def test_income_recalculates_with_daily_cap(self, client: AsyncClient):
        for _ in range(12):
            resp = await client.post(
                "/api/v1/sessions",
                headers={"Authorization": "Bearer tok"},
                json={"status": "BUYER_READY"},
            )
            assert resp.status_code == 200

        income_resp = await client.get(
            "/api/v1/income/today",
            headers={"Authorization": "Bearer tok"},
        )
        assert income_resp.status_code == 200
        income = income_resp.json()
        assert income["total_usd"] == 5.00
        assert income["sessions_uploaded"] == 12
        assert income["sessions_counted"] == 10

    async def test_auto_generates_session_id(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/sessions",
            headers={"Authorization": "Bearer tok"},
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["status"] == "received"

    async def test_401_without_bearer(self, client: AsyncClient):
        resp = await client.post("/api/v1/sessions", json={})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
class TestCORS:
    async def test_cors_headers_present(self, client: AsyncClient):
        resp = await client.options(
            "/api/v1/income/today",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # FastAPI CORS middleware handles preflight
        assert resp.status_code in (200, 204)
