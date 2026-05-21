"""Tests for backend_stub.tester_invite endpoints."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend_stub import tester_invite as ti
from backend_stub.main import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ADMIN_TOKEN = "test-admin-token-xyz"


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch: pytest.MonkeyPatch):
    """Override admin token for all tests."""
    monkeypatch.setenv("TESTER_ADMIN_TOKEN", ADMIN_TOKEN)


@pytest.fixture(autouse=True)
def _clear_store():
    """Clear the tester store before and after each test."""
    ti.get_store().clear()
    yield
    ti.get_store().clear()


@pytest_asyncio.fixture
async def client():
    """Async test client."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


# ---------------------------------------------------------------------------
# POST /api/v1/testers/apply
# ---------------------------------------------------------------------------


class TestApplyTester:
    async def test_apply_returns_200_and_tester_id(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "howard@example.com",
                "discord_user": "howard#1234",
                "why_interested": "I want to test the pipeline",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["tester_id"].startswith("tst-")

    async def test_apply_invalid_email(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "not-an-email",
                "discord_user": "howard#1234",
                "why_interested": "testing",
            },
        )
        assert resp.status_code == 400
        assert "Invalid email" in resp.json()["detail"]

    async def test_apply_missing_email(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "discord_user": "howard#1234",
                "why_interested": "testing",
            },
        )
        assert resp.status_code == 400
        assert "email is required" in resp.json()["detail"]

    async def test_apply_missing_discord(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "howard@example.com",
                "why_interested": "testing",
            },
        )
        assert resp.status_code == 400
        assert "discord_user is required" in resp.json()["detail"]

    async def test_apply_missing_why(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "howard@example.com",
                "discord_user": "howard#1234",
            },
        )
        assert resp.status_code == 400
        assert "why_interested is required" in resp.json()["detail"]

    async def test_apply_no_auth_required(self, client: AsyncClient):
        """Apply endpoint is public – no Bearer needed."""
        resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "anon@example.com",
                "discord_user": "anon#0000",
                "why_interested": "curious",
            },
        )
        assert resp.status_code == 200

    async def test_apply_multiple_unique_ids(self, client: AsyncClient):
        ids = set()
        for i in range(5):
            resp = await client.post(
                "/api/v1/testers/apply",
                json={
                    "email": f"user{i}@example.com",
                    "discord_user": f"user{i}#000{i}",
                    "why_interested": f"reason {i}",
                },
            )
            assert resp.status_code == 200
            ids.add(resp.json()["tester_id"])
        assert len(ids) == 5


# ---------------------------------------------------------------------------
# GET /api/v1/testers
# ---------------------------------------------------------------------------


class TestListTesters:
    async def test_list_requires_admin(self, client: AsyncClient):
        resp = await client.get("/api/v1/testers")
        assert resp.status_code == 401

    async def test_list_wrong_token(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/testers",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 403

    async def test_list_empty(self, client: AsyncClient):
        resp = await client.get("/api/v1/testers", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_returns_applicants(self, client: AsyncClient):
        # Apply two testers
        await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "a@example.com",
                "discord_user": "a#1111",
                "why_interested": "reason a",
            },
        )
        await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "b@example.com",
                "discord_user": "b#2222",
                "why_interested": "reason b",
            },
        )
        resp = await client.get("/api/v1/testers", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        emails = {d["email"] for d in data}
        assert "a@example.com" in emails
        assert "b@example.com" in emails


# ---------------------------------------------------------------------------
# POST /api/v1/testers/{id}/approve
# ---------------------------------------------------------------------------


class TestApproveTester:
    async def test_approve_returns_200_and_url(self, client: AsyncClient):
        apply_resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "approved@example.com",
                "discord_user": "approved#9999",
                "why_interested": "ready",
            },
        )
        tester_id = apply_resp.json()["tester_id"]

        resp = await client.post(
            f"/api/v1/testers/{tester_id}/approve",
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["tester_id"] == tester_id
        assert "download_url" in data
        assert data["download_url"].startswith("https://dl.example.com/beta")
        assert tester_id in data["download_url"]

    async def test_approve_requires_admin(self, client: AsyncClient):
        apply_resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "x@example.com",
                "discord_user": "x#0000",
                "why_interested": "x",
            },
        )
        tester_id = apply_resp.json()["tester_id"]

        resp = await client.post(f"/api/v1/testers/{tester_id}/approve")
        assert resp.status_code == 401

    async def test_approve_not_found(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/testers/tst-nonexistent/approve",
            headers=_admin_headers(),
        )
        assert resp.status_code == 404

    async def test_approve_already_approved(self, client: AsyncClient):
        apply_resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "dup@example.com",
                "discord_user": "dup#0000",
                "why_interested": "dup",
            },
        )
        tester_id = apply_resp.json()["tester_id"]

        # First approve
        resp1 = await client.post(
            f"/api/v1/testers/{tester_id}/approve",
            headers=_admin_headers(),
        )
        assert resp1.status_code == 200

        # Second approve should conflict
        resp2 = await client.post(
            f"/api/v1/testers/{tester_id}/approve",
            headers=_admin_headers(),
        )
        assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/v1/testers/{id}/reject
# ---------------------------------------------------------------------------


class TestRejectTester:
    async def test_reject_returns_200(self, client: AsyncClient):
        apply_resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "rejected@example.com",
                "discord_user": "rej#0000",
                "why_interested": "nope",
            },
        )
        tester_id = apply_resp.json()["tester_id"]

        resp = await client.post(
            f"/api/v1/testers/{tester_id}/reject",
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["tester_id"] == tester_id

    async def test_reject_requires_admin(self, client: AsyncClient):
        apply_resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "x@example.com",
                "discord_user": "x#0000",
                "why_interested": "x",
            },
        )
        tester_id = apply_resp.json()["tester_id"]

        resp = await client.post(f"/api/v1/testers/{tester_id}/reject")
        assert resp.status_code == 401

    async def test_reject_not_found(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/testers/tst-nonexistent/reject",
            headers=_admin_headers(),
        )
        assert resp.status_code == 404

    async def test_reject_already_rejected(self, client: AsyncClient):
        apply_resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "dup2@example.com",
                "discord_user": "dup2#0000",
                "why_interested": "dup2",
            },
        )
        tester_id = apply_resp.json()["tester_id"]

        resp1 = await client.post(
            f"/api/v1/testers/{tester_id}/reject",
            headers=_admin_headers(),
        )
        assert resp1.status_code == 200

        resp2 = await client.post(
            f"/api/v1/testers/{tester_id}/reject",
            headers=_admin_headers(),
        )
        assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# Full workflow: apply → approve → list
# ---------------------------------------------------------------------------


class TestFullWorkflow:
    async def test_apply_approve_list(self, client: AsyncClient):
        # 1. Apply
        apply_resp = await client.post(
            "/api/v1/testers/apply",
            json={
                "email": "workflow@example.com",
                "discord_user": "wf#1234",
                "why_interested": "full workflow test",
            },
        )
        assert apply_resp.status_code == 200
        tester_id = apply_resp.json()["tester_id"]

        # 2. Approve
        approve_resp = await client.post(
            f"/api/v1/testers/{tester_id}/approve",
            headers=_admin_headers(),
        )
        assert approve_resp.status_code == 200
        download_url = approve_resp.json()["download_url"]

        # 3. List and verify
        list_resp = await client.get("/api/v1/testers", headers=_admin_headers())
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert len(items) == 1
        item = items[0]
        assert item["status"] == "approved"
        assert item["download_url"] == download_url
        assert item["tester_id"] == tester_id


# ---------------------------------------------------------------------------
# Email validation unit tests
# ---------------------------------------------------------------------------


class TestEmailValidation:
    @pytest.mark.parametrize(
        "email,expected",
        [
            ("valid@example.com", True),
            ("user.name+tag@domain.co.uk", True),
            ("a@b.c", True),
            ("", False),
            ("no-at-sign", False),
            ("@missing-local.com", False),
            ("missing-domain@", False),
            ("spaces in@email.com", False),
        ],
    )
    def test_validate_email(self, email: str, expected: bool):
        assert ti._validate_email(email) is expected


# ---------------------------------------------------------------------------
# Signed URL generation
# ---------------------------------------------------------------------------


class TestSignedURL:
    def test_generates_url_with_tester_id(self):
        tid = "tst-abc123"
        url = ti._generate_signed_url(tid)
        assert url.startswith("https://dl.example.com/beta")
        assert f"tester={tid}" in url
        assert "sig=" in url

    def test_generates_unique_urls(self):
        tid = "tst-abc123"
        urls = {ti._generate_signed_url(tid) for _ in range(10)}
        assert len(urls) == 10  # each call has a unique sig
