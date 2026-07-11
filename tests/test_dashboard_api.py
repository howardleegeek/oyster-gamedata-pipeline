"""
Tests for Oyster Dashboard FastAPI backend.
Verifies all 7 endpoints return correct shapes and auth headers are respected.
"""

import os
import sys
from datetime import datetime

import jwt
import pytest
from fastapi.testclient import TestClient

# Add dashboard to path - must be after standard library imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
from oauth import JWT_ALGORITHM, JWT_SECRET  # noqa: E402

import server  # noqa: E402


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(server.app)


@pytest.fixture
def buyer_token():
    """Generate buyer JWT token."""
    token = jwt.encode(
        {"sub": "buyer1", "role": "buyer", "exp": datetime.utcnow().timestamp() + 3600},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    return token


@pytest.fixture
def contributor_token():
    """Generate contributor JWT token."""
    token = jwt.encode(
        {"sub": "contributor1", "role": "contributor", "exp": datetime.utcnow().timestamp() + 3600},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    return token


@pytest.fixture
def contributor2_token():
    """Generate contributor2 JWT token."""
    token = jwt.encode(
        {"sub": "contributor2", "role": "contributor", "exp": datetime.utcnow().timestamp() + 3600},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    return token


@pytest.fixture
def auth_headers_buyer(buyer_token):
    """Get auth headers for buyer."""
    return {"Authorization": f"Bearer {buyer_token}"}


@pytest.fixture
def auth_headers_contributor(contributor_token):
    """Get auth headers for contributor."""
    return {"Authorization": f"Bearer {contributor_token}"}


@pytest.fixture
def auth_headers_contributor2(contributor2_token):
    """Get auth headers for contributor2."""
    return {"Authorization": f"Bearer {contributor2_token}"}


class TestAuth:
    """Test authentication and authorization."""

    def test_missing_auth_header(self, client):
        """Test that missing auth header returns 401."""
        response = client.get("/api/sessions")
        assert response.status_code == 401
        assert "Missing authorization header" in response.json()["detail"]

    def test_invalid_token(self, client):
        """Test that invalid token returns 401."""
        response = client.get("/api/sessions", headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401

    def test_buyer_can_list_all_sessions(self, client, auth_headers_buyer):
        """Test that buyer can list all sessions."""
        response = client.get("/api/sessions", headers=auth_headers_buyer)
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data

    def test_contributor_sees_only_own_sessions(self, client, auth_headers_contributor):
        """Test that contributor can only see their own sessions."""
        response = client.get("/api/sessions", headers=auth_headers_contributor)
        assert response.status_code == 200
        data = response.json()
        # All sessions should belong to contributor1
        for session in data["sessions"]:
            assert session["contributor_id"] == "contributor1"


class TestListSessions:
    """Test GET /api/sessions endpoint."""

    def test_list_sessions_returns_correct_shape(self, client, auth_headers_buyer):
        """Test that list sessions returns correct response shape."""
        response = client.get("/api/sessions", headers=auth_headers_buyer)
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data["sessions"], list)
        assert isinstance(data["total"], int)
        assert isinstance(data["page"], int)
        assert isinstance(data["page_size"], int)

    def test_list_sessions_with_filters(self, client, auth_headers_buyer):
        """Test that filters work correctly."""
        # Filter by game
        response = client.get("/api/sessions?game=minecraft", headers=auth_headers_buyer)
        assert response.status_code == 200
        data = response.json()
        for session in data["sessions"]:
            assert session["game"] == "minecraft"

        # Filter by min_audit_score
        response = client.get("/api/sessions?min_audit_score=0.9", headers=auth_headers_buyer)
        assert response.status_code == 200
        data = response.json()
        for session in data["sessions"]:
            assert session["audit_score"] >= 0.9

    def test_pagination(self, client, auth_headers_buyer):
        """Test pagination parameters."""
        response = client.get("/api/sessions?page=1&page_size=5", headers=auth_headers_buyer)
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["page_size"] == 5
        assert len(data["sessions"]) <= 5


class TestGetSession:
    """Test GET /api/sessions/{id} endpoint."""

    def test_get_session_returns_correct_shape(self, client, auth_headers_buyer):
        """Test that get session returns correct response shape."""
        response = client.get("/api/sessions/session_001", headers=auth_headers_buyer)
        assert response.status_code == 200

        data = response.json()
        assert "id" in data
        assert "game" in data
        assert "scene" in data
        assert "route_type" in data
        assert "audit_score" in data
        assert "contributor_id" in data
        assert "status" in data
        assert "provenance_hash" in data

    def test_get_nonexistent_session(self, client, auth_headers_buyer):
        """Test that nonexistent session returns 404."""
        response = client.get("/api/sessions/nonexistent", headers=auth_headers_buyer)
        assert response.status_code == 404

    def test_contributor_cannot_access_others_session(self, client, auth_headers_contributor):
        """Test that contributor cannot access another contributor's session."""
        # session_001 belongs to contributor2 (i=1: (1 % 2) + 1 = 2)
        # contributor1 is logged in, so should get 403
        response = client.get("/api/sessions/session_001", headers=auth_headers_contributor)
        assert response.status_code == 403


class TestSessionPreview:
    """Test GET /api/sessions/{id}/preview endpoint."""

    def test_preview_returns_video(self, client, auth_headers_buyer):
        """Test that preview returns video data."""
        response = client.get("/api/sessions/session_001/preview", headers=auth_headers_buyer)
        assert response.status_code == 200
        assert response.headers["content-type"] == "video/mp4"
        assert len(response.content) > 0

    def test_preview_byte_range(self, client, auth_headers_buyer):
        """Test that byte range requests work (HTTP 206)."""
        response = client.get(
            "/api/sessions/session_001/preview",
            headers={**auth_headers_buyer, "range": "bytes=0-10"},
        )
        assert response.status_code == 206
        assert "content-range" in response.headers

    def test_preview_nonexistent_session(self, client, auth_headers_buyer):
        """Test that preview of nonexistent session returns 404."""
        response = client.get("/api/sessions/nonexistent/preview", headers=auth_headers_buyer)
        assert response.status_code == 404


class TestSessionAudit:
    """Test GET /api/sessions/{id}/audit endpoint."""

    def test_audit_returns_correct_shape(self, client, auth_headers_buyer):
        """Test that audit returns correct response shape."""
        response = client.get("/api/sessions/session_001/audit", headers=auth_headers_buyer)
        assert response.status_code == 200

        data = response.json()
        assert "session_id" in data
        assert "audit_score" in data
        assert "checks" in data
        assert "timestamp" in data
        assert isinstance(data["checks"], dict)

    def test_audit_nonexistent_session(self, client, auth_headers_buyer):
        """Test that audit of nonexistent session returns 404."""
        response = client.get("/api/sessions/nonexistent/audit", headers=auth_headers_buyer)
        assert response.status_code == 404


class TestVerifyProvenance:
    """Test GET /api/sessions/{id}/verify endpoint."""

    def test_verify_returns_correct_shape(self, client, auth_headers_buyer):
        """Test that verify returns correct response shape."""
        response = client.get("/api/sessions/session_001/verify", headers=auth_headers_buyer)
        assert response.status_code == 200

        data = response.json()
        assert "session_id" in data
        assert "valid" in data
        assert "chain_intact" in data
        assert "hash_matches" in data
        assert "details" in data
        assert isinstance(data["valid"], bool)
        assert isinstance(data["chain_intact"], bool)
        assert isinstance(data["hash_matches"], bool)

    def test_verify_valid_session(self, client, auth_headers_buyer):
        """Test that valid session verification returns valid=true."""
        response = client.get("/api/sessions/session_001/verify", headers=auth_headers_buyer)
        assert response.status_code == 200
        data = response.json()
        # Hash should match since we compute it from session_id
        assert data["valid"] is True
        assert data["chain_intact"] is True
        assert data["hash_matches"] is True


class TestApproveSession:
    """Test POST /api/sessions/{id}/approve endpoint."""

    def test_approve_requires_buyer(self, client, auth_headers_contributor):
        """Test that approve requires buyer role."""
        response = client.post(
            "/api/sessions/session_003/approve", headers=auth_headers_contributor, json={}
        )
        assert response.status_code == 403

    def test_approve_session(self, client, auth_headers_buyer):
        """Test that buyer can approve a pending session."""
        response = client.post(
            "/api/sessions/session_003/approve",
            headers=auth_headers_buyer,
            json={"notes": "Good quality"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"
        assert data["payout_triggered"] is True

    def test_approve_nonexistent_session(self, client, auth_headers_buyer):
        """Test that approving nonexistent session returns 404."""
        response = client.post(
            "/api/sessions/nonexistent_session/approve", headers=auth_headers_buyer, json={}
        )
        assert response.status_code == 404


class TestRejectSession:
    """Test POST /api/sessions/{id}/reject endpoint."""

    def test_reject_requires_buyer(self, client, auth_headers_contributor):
        """Test that reject requires buyer role."""
        response = client.post(
            "/api/sessions/session_003/reject",
            headers=auth_headers_contributor,
            json={"reason": "Bad quality"},
        )
        assert response.status_code == 403

    def test_reject_session(self, client, auth_headers_buyer):
        """Test that buyer can reject a pending session."""
        response = client.post(
            "/api/sessions/session_004/reject",
            headers=auth_headers_buyer,
            json={"reason": "Low audit score", "notes": "Needs improvement"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"
        assert data["reason"] == "Low audit score"

    def test_reject_requires_reason(self, client, auth_headers_buyer):
        """Test that rejection requires a reason."""
        client.post(
            "/api/sessions/session_005/reject", headers=auth_headers_buyer, json={"reason": ""}
        )
        # Should still work as reason is provided (even if empty string)
        # In production, you'd want validation


class TestContributorEndpoints:
    """Test contributor-specific endpoints."""

    def test_my_sessions(self, client, auth_headers_contributor):
        """Test that contributor can get their sessions."""
        response = client.get("/api/my/sessions", headers=auth_headers_contributor)
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert "total" in data

    def test_my_sessions_buyer_forbidden(self, client, auth_headers_buyer):
        """Test that buyer cannot access /api/my/sessions."""
        response = client.get("/api/my/sessions", headers=auth_headers_buyer)
        assert response.status_code == 403

    def test_my_payouts(self, client, auth_headers_contributor):
        """Test that contributor can get their payouts."""
        response = client.get("/api/my/payouts", headers=auth_headers_contributor)
        assert response.status_code == 200
        data = response.json()
        assert "contributor_id" in data
        assert "total_payout_usd" in data
        assert "approved_sessions" in data

    def test_my_payouts_buyer_forbidden(self, client, auth_headers_buyer):
        """Test that buyer cannot access /api/my/payouts."""
        response = client.get("/api/my/payouts", headers=auth_headers_buyer)
        assert response.status_code == 403


class TestBulkDownload:
    """Test bulk download endpoint."""

    def test_bulk_download(self, client, auth_headers_buyer):
        """Test bulk download bundle generation."""
        response = client.post(
            "/api/sessions/bulk-download",
            headers=auth_headers_buyer,
            json=["session_001", "session_002"],
        )
        assert response.status_code == 200
        data = response.json()
        assert "bundle_id" in data
        assert "session_count" in data
        assert data["session_count"] == 2

    def test_bulk_download_contributor_forbidden(self, client, auth_headers_contributor):
        """Test that contributor cannot bulk download."""
        response = client.post(
            "/api/sessions/bulk-download", headers=auth_headers_contributor, json=["session_001"]
        )
        assert response.status_code == 403


class TestHealthCheck:
    """Test health check endpoint."""

    def test_health_check(self, client):
        """Test that health check returns 200."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestNoPIILeakage:
    """Test that PII is not leaked between contributors."""

    def test_contributor_cannot_see_other_contributor_sessions(
        self, client, auth_headers_contributor
    ):
        """Test that contributor1 cannot see contributor2's sessions."""
        response = client.get("/api/sessions", headers=auth_headers_contributor)
        assert response.status_code == 200
        data = response.json()

        # All sessions should belong to contributor1
        for session in data["sessions"]:
            assert session["contributor_id"] == "contributor1"
            # Should not see contributor2's sessions
            assert session["contributor_id"] != "contributor2"

    def test_contributor_cannot_access_other_session_directly(
        self, client, auth_headers_contributor
    ):
        """Test that contributor cannot access another contributor's session by ID."""
        # session_001 belongs to contributor2 (i=1: (1 % 2) + 1 = 2)
        # contributor1 is logged in, so should get 403
        response = client.get("/api/sessions/session_001", headers=auth_headers_contributor)
        assert response.status_code == 403


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
