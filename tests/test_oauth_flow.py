"""Tests for OAuth flow, JWT verification, and role-based access control."""

import os
import sys
import time
import json
import hashlib
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime, timedelta

import pytest
import jwt
from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from httpx import Response

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.oauth import (
    router,
    verify_jwt_token,
    create_jwt_token,
    hash_oauth_id,
    oauth_states,
    users_db,
    refresh_tokens,
    JWT_SECRET,
    JWT_ALGORITHM,
    ACCESS_TOKEN_EXPIRE_HOURS,
)
from server.auth_middleware import (
    AuthMiddleware,
    get_current_user,
    require_role,
    require_buyer,
    require_contributor,
    require_admin,
)


# Test fixtures
@pytest.fixture
def app():
    """Create a test FastAPI app."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def clean_state():
    """Clean OAuth state before each test."""
    oauth_states.clear()
    users_db.clear()
    refresh_tokens.clear()
    yield
    oauth_states.clear()
    users_db.clear()
    refresh_tokens.clear()


# Mock OAuth responses
def mock_google_user_response():
    """Mock Google user info response."""
    return {
        "id": "google_12345",
        "email": "test@example.com",
        "name": "Test User",
        "picture": "https://example.com/photo.jpg"
    }


def mock_discord_user_response():
    """Mock Discord user info response."""
    return {
        "id": "discord_67890",
        "email": "test@example.com",
        "username": "testuser",
        "avatar": "abc123"
    }


def mock_token_response(access_token="mock_access", refresh_token="mock_refresh"):
    """Mock OAuth token response."""
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": 3600,
        "token_type": "Bearer"
    }


class TestGoogleOAuth:
    """Tests for Google OAuth flow."""
    
    @patch("server.oauth.GOOGLE_CLIENT_ID", "test_client_id")
    @patch("server.oauth.GOOGLE_CLIENT_SECRET", "test_secret")
    def test_google_login_redirects(self, client, clean_state):
        """Test that Google login endpoint redirects to Google OAuth."""
        response = client.get("/api/auth/google/login", follow_redirects=False)
        
        assert response.status_code == 307
        location = response.headers["location"]
        assert "accounts.google.com" in location
        assert "client_id=test_client_id" in location
        assert "response_type=code" in location
        assert "state=" in location
    
    @patch("server.oauth.GOOGLE_CLIENT_ID", "test_client_id")
    @patch("server.oauth.GOOGLE_CLIENT_SECRET", "test_secret")
    def test_google_login_with_redirect_param(self, client, clean_state):
        """Test Google login with custom redirect parameter."""
        response = client.get(
            "/api/auth/google/login?redirect=http://127.0.0.1:8080/callback",
            follow_redirects=False
        )
        
        assert response.status_code == 307
        # State should be stored with redirect
        assert len(oauth_states) == 1
    
    @patch("server.oauth.GOOGLE_CLIENT_ID", "test_client_id")
    @patch("server.oauth.GOOGLE_CLIENT_SECRET", "test_secret")
    @patch("server.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
    @patch("httpx.AsyncClient")
    def test_google_callback_creates_jwt(self, mock_client, client, clean_state):
        """Test that Google callback creates JWT correctly."""
        # Setup state
        import secrets
        state = secrets.token_urlsafe(16)
        oauth_states[state] = {
            "provider": "google",
            "redirect": None,
            "created_at": time.time()
        }
        
        # Mock HTTP responses
        mock_token_resp = Mock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = mock_token_response()
        
        mock_user_resp = Mock()
        mock_user_resp.status_code = 200
        mock_user_resp.json.return_value = mock_google_user_response()
        
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_token_resp
        mock_http_client.get.return_value = mock_user_resp
        mock_client.return_value.__aenter__.return_value = mock_http_client
        
        # Make callback request
        response = client.get(
            f"/api/auth/google/callback?code=test_code&state={state}"
        )
        
        # Should return JWT tokens
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        
        # Verify JWT claims
        payload = jwt.decode(data["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["sub"] is not None
        assert payload["email"] == "test@example.com"
        assert payload["role"] in ["buyer", "contributor", "admin"]
        assert payload["oauth_provider"] == "google"
        assert payload["oauth_id"] == "google_12345"
    
    def test_google_callback_invalid_state(self, client, clean_state):
        """Test that callback rejects invalid state."""
        response = client.get(
            "/api/auth/google/callback?code=test_code&state=invalid_state"
        )
        
        assert response.status_code == 400
        assert "Invalid state" in response.json()["detail"]


class TestDiscordOAuth:
    """Tests for Discord OAuth flow."""
    
    @patch("server.oauth.DISCORD_CLIENT_ID", "test_client_id")
    @patch("server.oauth.DISCORD_CLIENT_SECRET", "test_secret")
    def test_discord_login_redirects(self, client, clean_state):
        """Test that Discord login endpoint redirects to Discord OAuth."""
        response = client.get("/api/auth/discord/login", follow_redirects=False)
        
        assert response.status_code == 307
        location = response.headers["location"]
        assert "discord.com" in location
        assert "client_id=test_client_id" in location
        assert "response_type=code" in location
    
    @patch("server.oauth.DISCORD_CLIENT_ID", "test_client_id")
    @patch("server.oauth.DISCORD_CLIENT_SECRET", "test_secret")
    @patch("server.oauth.DISCORD_REDIRECT_URI", "http://localhost:8000/api/auth/discord/callback")
    @patch("httpx.AsyncClient")
    def test_discord_callback_creates_jwt(self, mock_client, client, clean_state):
        """Test that Discord callback creates JWT correctly."""
        import secrets
        state = secrets.token_urlsafe(16)
        oauth_states[state] = {
            "provider": "discord",
            "redirect": None,
            "created_at": time.time()
        }
        
        mock_token_resp = Mock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = mock_token_response()
        
        mock_user_resp = Mock()
        mock_user_resp.status_code = 200
        mock_user_resp.json.return_value = mock_discord_user_response()
        
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_token_resp
        mock_http_client.get.return_value = mock_user_resp
        mock_client.return_value.__aenter__.return_value = mock_http_client
        
        response = client.get(
            f"/api/auth/discord/callback?code=test_code&state={state}"
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        
        payload = jwt.decode(data["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["oauth_provider"] == "discord"
        assert payload["oauth_id"] == "discord_67890"


class TestJWTVerification:
    """Tests for JWT verification."""
    
    def test_verify_valid_token(self, clean_state):
        """Test that valid JWT token is verified correctly."""
        token = create_jwt_token(
            user_id="test_user",
            email="test@example.com",
            role="buyer",
            provider="google",
            oauth_id="google_123"
        )
        
        payload = verify_jwt_token(token)
        
        assert payload["sub"] == "test_user"
        assert payload["email"] == "test@example.com"
        assert payload["role"] == "buyer"
        assert payload["oauth_provider"] == "google"
    
    def test_verify_expired_token(self, clean_state):
        """Test that expired token is rejected."""
        # Create token that's already expired
        now = datetime.utcnow()
        payload = {
            "sub": "test_user",
            "email": "test@example.com",
            "role": "buyer",
            "oauth_provider": "google",
            "oauth_id": "google_123",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),  # Expired 1 hour ago
            "type": "access"
        }
        
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        
        with pytest.raises(HTTPException) as exc_info:
            verify_jwt_token(token)
        
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()
    
    def test_verify_tampered_token(self, clean_state):
        """Test that tampered token is rejected."""
        token = create_jwt_token(
            user_id="test_user",
            email="test@example.com",
            role="buyer",
            provider="google",
            oauth_id="google_123"
        )
        
        # Tamper with the token
        parts = token.split('.')
        if len(parts) == 3:
            # Modify the payload
            tampered_payload = parts[1][:-1] + 'X'  # Change last char
            tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"
            
            with pytest.raises(HTTPException) as exc_info:
                verify_jwt_token(tampered_token)
            
            assert exc_info.value.status_code == 401
    
    def test_verify_token_wrong_type(self, clean_state):
        """Test that token with wrong type is rejected."""
        now = datetime.utcnow()
        payload = {
            "sub": "test_user",
            "email": "test@example.com",
            "role": "buyer",
            "oauth_provider": "google",
            "oauth_id": "google_123",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "type": "refresh"  # Wrong type
        }
        
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        
        with pytest.raises(HTTPException) as exc_info:
            verify_jwt_token(token)
        
        assert exc_info.value.status_code == 401
        assert "Invalid token type" in exc_info.value.detail


class TestRoleBasedAccessControl:
    """Tests for role-based access control."""
    
    @pytest.fixture
    def protected_app(self):
        """Create app with protected routes."""
        app = FastAPI()
        
        @app.get("/buyer-only")
        async def buyer_endpoint(user = Depends(require_buyer)):
            return {"message": "buyer access", "user": user}
        
        @app.get("/contributor-only")
        async def contributor_endpoint(user = Depends(require_contributor)):
            return {"message": "contributor access", "user": user}
        
        @app.get("/admin-only")
        async def admin_endpoint(user = Depends(require_admin)):
            return {"message": "admin access", "user": user}
        
        return app
    
    @pytest.fixture
    def protected_client(self, protected_app):
        """Create client for protected routes."""
        return TestClient(protected_app)
    
    def test_buyer_access_buyer_endpoint(self, protected_client, clean_state):
        """Test that buyer can access buyer-only endpoint."""
        token = create_jwt_token(
            user_id="buyer_user",
            email="buyer@example.com",
            role="buyer",
            provider="google",
            oauth_id="google_buyer"
        )
        
        response = protected_client.get(
            "/buyer-only",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        assert response.json()["message"] == "buyer access"
    
    def test_contributor_access_buyer_endpoint(self, protected_client, clean_state):
        """Test that contributor can access buyer-only endpoint."""
        token = create_jwt_token(
            user_id="contrib_user",
            email="contrib@example.com",
            role="contributor",
            provider="discord",
            oauth_id="discord_contrib"
        )
        
        response = protected_client.get(
            "/buyer-only",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
    
    def test_buyer_rejected_from_contributor_endpoint(self, protected_client, clean_state):
        """Test that buyer JWT is rejected from contributor-only endpoint."""
        token = create_jwt_token(
            user_id="buyer_user",
            email="buyer@example.com",
            role="buyer",
            provider="google",
            oauth_id="google_buyer"
        )
        
        response = protected_client.get(
            "/contributor-only",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 403
        assert "not authorized" in response.json()["detail"].lower()
    
    def test_contributor_access_contributor_endpoint(self, protected_client, clean_state):
        """Test that contributor can access contributor-only endpoint."""
        token = create_jwt_token(
            user_id="contrib_user",
            email="contrib@example.com",
            role="contributor",
            provider="discord",
            oauth_id="discord_contrib"
        )
        
        response = protected_client.get(
            "/contributor-only",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
    
    def test_admin_access_all_endpoints(self, protected_client, clean_state):
        """Test that admin can access all endpoints."""
        token = create_jwt_token(
            user_id="admin_user",
            email="admin@example.com",
            role="admin",
            provider="google",
            oauth_id="google_admin"
        )
        
        for endpoint in ["/buyer-only", "/contributor-only", "/admin-only"]:
            response = protected_client.get(
                endpoint,
                headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200, f"Failed for {endpoint}"


class TestTokenRefresh:
    """Tests for token refresh functionality."""
    
    def test_refresh_valid_token(self, client, clean_state):
        """Test refreshing a valid refresh token."""
        # Create a user and refresh token
        user_hash = hash_oauth_id("google", "test_123")
        users_db[user_hash] = {
            "id": user_hash,
            "oauth_provider": "google",
            "oauth_id_hash": user_hash,
            "email_encrypted": "test@example.com",
            "role": "buyer"
        }
        
        import secrets
        refresh_token = secrets.token_urlsafe(32)
        refresh_tokens[refresh_token] = {
            "user_id": user_hash,
            "created_at": time.time(),
            "expires_at": time.time() + 7 * 24 * 3600
        }
        
        response = client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        
        # Verify new access token
        payload = jwt.decode(data["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["sub"] == user_hash
        assert payload["role"] == "buyer"
    
    def test_refresh_invalid_token(self, client, clean_state):
        """Test that invalid refresh token is rejected."""
        response = client.post(
            "/api/auth/refresh",
            json={"refresh_token": "invalid_token"}
        )
        
        assert response.status_code == 401
    
    def test_refresh_expired_token(self, client, clean_state):
        """Test that expired refresh token is rejected."""
        import secrets
        refresh_token = secrets.token_urlsafe(32)
        refresh_tokens[refresh_token] = {
            "user_id": "test_user",
            "created_at": time.time() - 8 * 24 * 3600,  # 8 days ago
            "expires_at": time.time() - 1 * 24 * 3600   # Expired 1 day ago
        }
        
        response = client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token}
        )
        
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()


class TestLogout:
    """Tests for logout functionality."""
    
    def test_logout_invalidates_refresh_token(self, client, clean_state):
        """Test that logout invalidates refresh token."""
        import secrets
        refresh_token = secrets.token_urlsafe(32)
        refresh_tokens[refresh_token] = {
            "user_id": "test_user",
            "created_at": time.time(),
            "expires_at": time.time() + 7 * 24 * 3600
        }
        
        response = client.post(
            "/api/auth/logout",
            json={"refresh_token": refresh_token}
        )
        
        assert response.status_code == 200
        assert refresh_token not in refresh_tokens
    
    def test_logout_with_invalid_token(self, client, clean_state):
        """Test logout with invalid token still succeeds."""
        response = client.post(
            "/api/auth/logout",
            json={"refresh_token": "nonexistent_token"}
        )
        
        # Should still succeed (idempotent)
        assert response.status_code == 200


class TestPrivacy:
    """Tests for privacy-preserving features."""
    
    def test_oauth_id_hashing(self, clean_state):
        """Test that OAuth IDs are properly hashed."""
        hash1 = hash_oauth_id("google", "user123")
        hash2 = hash_oauth_id("google", "user456")
        hash3 = hash_oauth_id("discord", "user123")
        
        # Same ID with different providers should have different hashes
        assert hash1 != hash3
        
        # Different IDs should have different hashes
        assert hash1 != hash2
        
        # Hash should be deterministic
        assert hash1 == hash_oauth_id("google", "user123")
        
        # Hash should be SHA-256 (64 hex chars)
        assert len(hash1) == 64
    
    def test_jwt_expiry_times(self, clean_state):
        """Test that JWT tokens have correct expiry times."""
        token = create_jwt_token(
            user_id="test_user",
            email="test@example.com",
            role="buyer",
            provider="google",
            oauth_id="google_123"
        )
        
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        
        # Access token should expire in 1 hour
        exp_time = datetime.fromtimestamp(payload["exp"])
        iat_time = datetime.fromtimestamp(payload["iat"])
        delta = exp_time - iat_time
        
        assert delta.total_seconds() == ACCESS_TOKEN_EXPIRE_HOURS * 3600


class TestLoopbackOAuth:
    """Tests for loopback OAuth (desktop app) functionality."""
    
    @patch("server.oauth.GOOGLE_CLIENT_ID", "test_client_id")
    @patch("server.oauth.GOOGLE_CLIENT_SECRET", "test_secret")
    @patch("server.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
    @patch("httpx.AsyncClient")
    def test_loopback_redirect(self, mock_client, client, clean_state):
        """Test that loopback redirect URLs are handled correctly."""
        import secrets
        state = secrets.token_urlsafe(16)
        loopback_url = "http://127.0.0.1:8080/callback"
        oauth_states[state] = {
            "provider": "google",
            "redirect": loopback_url,
            "created_at": time.time()
        }
        
        mock_token_resp = Mock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = mock_token_response()
        
        mock_user_resp = Mock()
        mock_user_resp.status_code = 200
        mock_user_resp.json.return_value = mock_google_user_response()
        
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_token_resp
        mock_http_client.get.return_value = mock_user_resp
        mock_client.return_value.__aenter__.return_value = mock_http_client
        
        response = client.get(
            f"/api/auth/google/callback?code=test_code&state={state}",
            follow_redirects=False
        )
        
        # Should redirect to loopback URL with tokens
        assert response.status_code == 307
        location = response.headers["location"]
        assert location.startswith(loopback_url)
        assert "access_token=" in location
        assert "refresh_token=" in location


if __name__ == "__main__":
    pytest.main([__file__, "-v"])