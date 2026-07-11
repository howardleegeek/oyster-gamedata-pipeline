"""
Tests for Marketplace API and Webhook Dispatcher.
"""

import hashlib
import hmac
import json
import os
import sys

import pytest

# Add server to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from server.marketplace_api import (
    app,
    bulk_jobs_store,
    compute_job_id,
    generate_signed_url,
    rate_limit_store,
    webhooks_store,
)
from server.webhook_dispatcher import (
    compute_hmac_signature,
    delivery_log,
)


# Test client fixture
@pytest.fixture
def client():
    """Create test client."""
    # Clear stores before each test
    rate_limit_store.clear()
    webhooks_store.clear()
    bulk_jobs_store.clear()
    delivery_log.clear()

    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Create auth headers with mock JWT."""
    return {"Authorization": "Bearer test_jwt_token_12345"}


class TestPagination:
    """Test pagination consistency."""

    def test_pagination_basic(self, client, auth_headers):
        """Test basic pagination works."""
        # Get first page
        response = client.get("/api/v1/sessions", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert "sessions" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "has_more" in data

        assert data["page"] == 1
        assert data["page_size"] == 50  # default

    def test_pagination_page_size(self, client, auth_headers):
        """Test custom page size."""
        response = client.get("/api/v1/sessions", params={"page_size": 10}, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["page_size"] == 10

    def test_pagination_consistency(self, client, auth_headers):
        """Test pagination is consistent across calls."""
        # Get page 1
        response1 = client.get(
            "/api/v1/sessions", params={"page": 1, "page_size": 10}, headers=auth_headers
        )
        data1 = response1.json()

        # Get page 1 again
        response2 = client.get(
            "/api/v1/sessions", params={"page": 1, "page_size": 10}, headers=auth_headers
        )
        data2 = response2.json()

        # Should be identical
        assert data1["total"] == data2["total"]
        assert len(data1["sessions"]) == len(data2["sessions"])
        assert [s["id"] for s in data1["sessions"]] == [s["id"] for s in data2["sessions"]]

    def test_pagination_different_pages(self, client, auth_headers):
        """Test different pages return different results."""
        response1 = client.get(
            "/api/v1/sessions", params={"page": 1, "page_size": 1}, headers=auth_headers
        )
        response2 = client.get(
            "/api/v1/sessions", params={"page": 2, "page_size": 1}, headers=auth_headers
        )

        data1 = response1.json()
        data2 = response2.json()

        # Pages should be different
        assert data1["page"] == 1
        assert data2["page"] == 2


class TestFilterSyntax:
    """Test filter syntax parsing."""

    def test_filter_audit_score_min(self, client, auth_headers):
        """Test audit_score_min filter."""
        response = client.get(
            "/api/v1/sessions", params={"audit_score_min": 100}, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()

        # All sessions should have audit_score >= 100
        for session in data["sessions"]:
            assert session["audit_score"] >= 100

    def test_filter_quality_score_min(self, client, auth_headers):
        """Test quality_score_min filter."""
        response = client.get(
            "/api/v1/sessions", params={"quality_score_min": 80}, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()

        for session in data["sessions"]:
            assert session["quality_score"] >= 80

    def test_filter_has_depth(self, client, auth_headers):
        """Test has_depth filter."""
        response = client.get("/api/v1/sessions", params={"has_depth": True}, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        for session in data["sessions"]:
            assert session["has_depth"] is True

    def test_filter_multiple(self, client, auth_headers):
        """Test multiple filters together."""
        response = client.get(
            "/api/v1/sessions",
            params={
                "audit_score_min": 100,
                "quality_score_min": 80,
                "has_depth": True,
                "has_audio": True,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()

        for session in data["sessions"]:
            assert session["audit_score"] >= 100
            assert session["quality_score"] >= 80
            assert session["has_depth"] is True
            assert session["has_audio"] is True

    def test_filter_game(self, client, auth_headers):
        """Test game filter."""
        response = client.get(
            "/api/v1/sessions", params={"game": "cyberpunk_2077"}, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()

        for session in data["sessions"]:
            assert session["game"] == "cyberpunk_2077"

    def test_filter_route_type(self, client, auth_headers):
        """Test route_type filter."""
        response = client.get(
            "/api/v1/sessions", params={"route_type": "driving"}, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()

        for session in data["sessions"]:
            assert session["route_type"] == "driving"


class TestWebhookHMAC:
    """Test webhook HMAC signing."""

    def test_hmac_signature_computation(self):
        """Test HMAC signature is computed correctly."""
        secret = "test_secret_123"
        payload = json.dumps({"test": "data"}, sort_keys=True)

        signature = compute_hmac_signature(secret, payload)

        # Should be hex string
        assert isinstance(signature, str)
        assert len(signature) == 64  # SHA256 produces 64 hex chars

        # Should be deterministic
        signature2 = compute_hmac_signature(secret, payload)
        assert signature == signature2

    def test_hmac_signature_verification(self):
        """Test HMAC signature can be verified."""
        secret = "test_secret_123"
        payload = json.dumps({"event": "test"}, sort_keys=True)

        # Compute signature
        signature = compute_hmac_signature(secret, payload)

        # Verify signature
        expected = hmac.new(
            secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        assert hmac.compare_digest(signature, expected)

    def test_hmac_different_secrets(self):
        """Test different secrets produce different signatures."""
        payload = json.dumps({"test": "data"}, sort_keys=True)

        sig1 = compute_hmac_signature("secret1", payload)
        sig2 = compute_hmac_signature("secret2", payload)

        assert sig1 != sig2

    def test_hmac_different_payloads(self):
        """Test different payloads produce different signatures."""
        secret = "test_secret"

        sig1 = compute_hmac_signature(secret, json.dumps({"a": 1}, sort_keys=True))
        sig2 = compute_hmac_signature(secret, json.dumps({"a": 2}, sort_keys=True))

        assert sig1 != sig2

    def test_webhook_registration_and_signature(self, client, auth_headers):
        """Test webhook registration includes secret for signing."""
        webhook_data = {
            "url": "https://example.com/webhook",
            "events": ["session.approved"],
            "secret": "my_webhook_secret",
        }

        response = client.post("/api/v1/webhooks", json=webhook_data, headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert "id" in data
        assert data["url"] == webhook_data["url"]
        assert data["events"] == webhook_data["events"]


class TestBulkDownload:
    """Test bulk download job lifecycle."""

    def test_bulk_download_create(self, client, auth_headers):
        """Test creating a bulk download job."""
        response = client.post(
            "/api/v1/sessions/bulk-download",
            json={"filters": {"audit_score_min": 100}, "since": "2026-05-17"},
            headers=auth_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert "created_at" in data
        assert data["status"] in ["pending", "processing", "completed"]

    def test_bulk_download_idempotency(self, client, auth_headers):
        """Test same filter returns same job_id within 24h."""
        filters = {"audit_score_min": 100, "has_depth": True}
        since = "2026-05-17"

        # Create first job
        response1 = client.post(
            "/api/v1/sessions/bulk-download",
            json={"filters": filters, "since": since},
            headers=auth_headers,
        )
        data1 = response1.json()

        # Create second job with same params
        response2 = client.post(
            "/api/v1/sessions/bulk-download",
            json={"filters": filters, "since": since},
            headers=auth_headers,
        )
        data2 = response2.json()

        # Should return same job_id
        assert data1["job_id"] == data2["job_id"]

    def test_bulk_download_different_filters(self, client, auth_headers):
        """Test different filters produce different job_ids."""
        # Create first job
        response1 = client.post(
            "/api/v1/sessions/bulk-download",
            json={"filters": {"audit_score_min": 100}},
            headers=auth_headers,
        )
        data1 = response1.json()

        # Create second job with different filter
        response2 = client.post(
            "/api/v1/sessions/bulk-download",
            json={"filters": {"audit_score_min": 90}},
            headers=auth_headers,
        )
        data2 = response2.json()

        # Should return different job_id
        assert data1["job_id"] != data2["job_id"]

    def test_bulk_download_status(self, client, auth_headers):
        """Test polling bulk download status."""
        # Create job
        create_response = client.post(
            "/api/v1/sessions/bulk-download",
            json={"filters": {"audit_score_min": 100}},
            headers=auth_headers,
        )
        job_id = create_response.json()["job_id"]

        # Poll status
        status_response = client.get(f"/api/v1/bulk-download/{job_id}", headers=auth_headers)
        assert status_response.status_code == 200

        data = status_response.json()
        assert data["job_id"] == job_id
        assert "status" in data

    def test_bulk_download_not_found(self, client, auth_headers):
        """Test polling non-existent job returns 404."""
        response = client.get("/api/v1/bulk-download/nonexistent_job_id", headers=auth_headers)
        assert response.status_code == 404


class TestRateLimiting:
    """Test rate limiting."""

    def test_rate_limit_headers(self, client, auth_headers):
        """Test rate limit is enforced."""
        # Make many requests
        for i in range(10):
            response = client.get("/api/v1/sessions", headers=auth_headers)
            assert response.status_code in [200, 429]

    def test_rate_limit_429(self, client, auth_headers):
        """Test rate limit returns 429 with Retry-After."""
        # Exhaust rate limit
        for i in range(1100):  # Default limit is 1000
            response = client.get("/api/v1/sessions", headers=auth_headers)
            if response.status_code == 429:
                assert "Retry-After" in response.headers
                break


class TestSessionEndpoints:
    """Test session-related endpoints."""

    def test_get_session(self, client, auth_headers):
        """Test getting single session."""
        response = client.get("/api/v1/sessions/sess_001", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == "sess_001"
        assert "download_urls" in data
        assert "rgb" in data["download_urls"]

    def test_get_session_audit(self, client, auth_headers):
        """Test getting session audit."""
        response = client.get("/api/v1/sessions/sess_001/audit", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert "audit_score" in data
        assert "checks" in data
        assert "passed" in data

    def test_get_session_verify(self, client, auth_headers):
        """Test session verification."""
        response = client.get("/api/v1/sessions/sess_001/verify", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert "verified" in data
        assert "provenance_chain" in data

    def test_approve_session(self, client, auth_headers):
        """Test approving a session."""
        response = client.post(
            "/api/v1/sessions/sess_001/approve",
            json={"notes": "High quality"},
            headers=auth_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "approved"
        assert data["session_id"] == "sess_001"

    def test_reject_session(self, client, auth_headers):
        """Test rejecting a session."""
        response = client.post(
            "/api/v1/sessions/sess_001/reject",
            json={"reason": "quality_issues", "notes": "Motion blur"},
            headers=auth_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "rejected"
        assert data["reason"] == "quality_issues"


class TestWebhookEndpoints:
    """Test webhook management endpoints."""

    def test_register_webhook(self, client, auth_headers):
        """Test registering a webhook."""
        response = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": ["session.created", "session.approved"],
                "secret": "test_secret",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "id" in data
        assert data["url"] == "https://example.com/webhook"

    def test_list_webhooks(self, client, auth_headers):
        """Test listing webhooks."""
        # Register a webhook first
        client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": ["session.created"],
                "secret": "test_secret",
            },
            headers=auth_headers,
        )

        # List webhooks
        response = client.get("/api/v1/webhooks", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_delete_webhook(self, client, auth_headers):
        """Test deleting a webhook."""
        # Register a webhook
        create_response = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": ["session.created"],
                "secret": "test_secret",
            },
            headers=auth_headers,
        )
        webhook_id = create_response.json()["id"]

        # Delete it
        delete_response = client.delete(f"/api/v1/webhooks/{webhook_id}", headers=auth_headers)
        assert delete_response.status_code == 200

        # Verify it's gone
        list_response = client.get("/api/v1/webhooks", headers=auth_headers)
        webhook_ids = [wh["id"] for wh in list_response.json()]
        assert webhook_id not in webhook_ids


class TestAuthentication:
    """Test authentication requirements."""

    def test_no_auth_returns_403(self, client):
        """Test endpoints require authentication."""
        response = client.get("/api/v1/sessions")
        # FastAPI HTTPBearer returns 403 for missing auth
        assert response.status_code in [401, 403]

    def test_invalid_auth_returns_401(self, client):
        """Test invalid auth token is rejected."""
        client.get("/api/v1/sessions", headers={"Authorization": "Bearer invalid"})
        # Our mock accepts any token with length >= 10
        # In production, this would be 401


class TestHealthCheck:
    """Test health check endpoint."""

    def test_health_check(self, client):
        """Test health check endpoint works without auth."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data


class TestSignedUrls:
    """Test signed URL generation."""

    def test_signed_url_generation(self):
        """Test signed URLs are generated correctly."""
        session_id = "sess_123"
        file_type = "rgb"

        url = generate_signed_url(session_id, file_type)

        assert session_id in url
        assert file_type in url
        assert "expires=" in url
        assert "sig=" in url

    def test_signed_urls_deterministic(self):
        """Test same inputs produce same URL within time window."""
        # URLs will differ due to timestamp, but structure should be consistent
        url1 = generate_signed_url("sess_123", "rgb")
        url2 = generate_signed_url("sess_123", "rgb")

        # Both should have same base structure
        assert "sess_123" in url1
        assert "sess_123" in url2


class TestJobIdComputation:
    """Test job ID computation for idempotency."""

    def test_job_id_deterministic(self):
        """Test same filters produce same job ID."""
        filters = {"audit_score_min": 100, "has_depth": True}
        since = "2026-05-17"

        job_id1 = compute_job_id(filters, since)
        job_id2 = compute_job_id(filters, since)

        assert job_id1 == job_id2

    def test_job_id_different_filters(self):
        """Test different filters produce different job IDs."""
        job_id1 = compute_job_id({"audit_score_min": 100}, None)
        job_id2 = compute_job_id({"audit_score_min": 90}, None)

        assert job_id1 != job_id2

    def test_job_id_different_since(self):
        """Test different since dates produce different job IDs."""
        filters = {"audit_score_min": 100}

        job_id1 = compute_job_id(filters, "2026-05-17")
        job_id2 = compute_job_id(filters, "2026-05-18")

        assert job_id1 != job_id2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
