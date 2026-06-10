#!/usr/bin/env python3
"""
Tests for OAuth login server.
"""

import base64
import hashlib
import json
import os

# Import the module to test
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from bin.oauth_login_server import (
    DEFAULT_PORT,
    PROVIDERS,
    OAuthLoginServer,
)


class TestOAuthLoginServer:
    """Test OAuthLoginServer class."""

    def setup_method(self):
        """Setup for each test."""
        # Mock environment variables
        self.env_patcher = patch.dict(
            os.environ,
            {
                "OYSTER_GOOGLE_CLIENT_ID": "test-google-client-id",
                "OYSTER_GOOGLE_CLIENT_SECRET": "test-google-client-secret",
                "OYSTER_DISCORD_CLIENT_ID": "test-discord-client-id",
                "OYSTER_DISCORD_CLIENT_SECRET": "test-discord-client-secret",
            },
        )
        self.env_patcher.start()

        # Create temp directory for auth file
        self.temp_dir = tempfile.mkdtemp()
        self.auth_dir = Path(self.temp_dir) / ".oyster"
        self.auth_file = self.auth_dir / "auth.json"

        # Patch AUTH_DIR and AUTH_FILE
        self.auth_dir_patcher = patch("bin.oauth_login_server.AUTH_DIR", self.auth_dir)
        self.auth_file_patcher = patch("bin.oauth_login_server.AUTH_FILE", self.auth_file)
        self.auth_dir_patcher.start()
        self.auth_file_patcher.start()

    def teardown_method(self):
        """Cleanup after each test."""
        self.env_patcher.stop()
        self.auth_dir_patcher.stop()
        self.auth_file_patcher.stop()

        # Clean up temp directory
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_init_with_valid_credentials(self):
        """Test initialization with valid credentials."""
        server = OAuthLoginServer("google")
        assert server.provider == "google"
        assert server.client_id == "test-google-client-id"
        assert server.client_secret == "test-google-client-secret"
        assert server.config == PROVIDERS["google"]
        assert len(server.code_verifier) >= 43  # PKCE code verifier length
        assert len(server.state) >= 16  # State should be at least 16 chars

        server = OAuthLoginServer("discord")
        assert server.provider == "discord"
        assert server.client_id == "test-discord-client-id"
        assert server.client_secret == "test-discord-client-secret"
        assert server.config == PROVIDERS["discord"]

    def test_init_missing_env_vars(self):
        """Test initialization with missing environment variables."""
        # Remove Google env vars
        os.environ.pop("OYSTER_GOOGLE_CLIENT_ID", None)

        with pytest.raises(ValueError, match="Missing OYSTER_GOOGLE_CLIENT_ID"):
            OAuthLoginServer("google")

        # Restore and test missing secret
        os.environ["OYSTER_GOOGLE_CLIENT_ID"] = "test-google-client-id"
        os.environ.pop("OYSTER_GOOGLE_CLIENT_SECRET", None)

        with pytest.raises(ValueError, match="Missing OYSTER_GOOGLE_CLIENT_SECRET"):
            OAuthLoginServer("google")

    def test_generate_code_challenge(self):
        """Test PKCE code challenge generation."""
        server = OAuthLoginServer("google")

        # Test with a known verifier
        verifier = "test_verifier_1234567890"
        challenge = server._generate_code_challenge(verifier)

        # Calculate expected challenge
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")

        assert challenge == expected
        assert "=" not in challenge  # No padding

    def test_get_auth_url(self):
        """Test OAuth authorization URL generation."""
        server = OAuthLoginServer("google")
        auth_url = server.get_auth_url()

        # Parse URL and query parameters
        parsed = urlparse(auth_url)
        query_params = parse_qs(parsed.query)

        assert parsed.scheme == "https"
        assert parsed.netloc == "accounts.google.com"
        assert parsed.path == "/o/oauth2/v2/auth"

        # Check query parameters
        assert query_params["client_id"] == ["test-google-client-id"]
        assert query_params["redirect_uri"] == [f"http://localhost:{DEFAULT_PORT}/oauth/callback"]
        assert query_params["response_type"] == ["code"]
        assert query_params["scope"] == ["openid email profile"]
        assert query_params["state"] == [server.state]
        assert query_params["code_challenge"] == [server.code_challenge]
        assert query_params["code_challenge_method"] == ["S256"]

    def test_calculate_expiry(self):
        """Test token expiry calculation."""
        server = OAuthLoginServer("google")

        # Test with expires_in
        token_data = {"expires_in": 7200}  # 2 hours
        expiry = server._calculate_expiry(token_data)

        # Should be a valid ISO format datetime
        dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        assert dt.tzinfo == timezone.utc

        # Test default (1 hour)
        token_data = {}
        expiry = server._calculate_expiry(token_data)
        dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        assert dt.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_exchange_code_for_token_success(self):
        """Test successful token exchange."""
        server = OAuthLoginServer("google")

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "test_access_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid email profile",
            "id_token": "test_id_token",
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            token_data = await server._exchange_code_for_token("test_code")

            assert token_data["access_token"] == "test_access_token"
            assert token_data["token_type"] == "Bearer"

            # Verify request parameters
            mock_post.assert_called_once()
            call_args = mock_post.call_args

            # Check URL
            assert call_args[0][0] == PROVIDERS["google"]["token_url"]

            # Check data includes PKCE code verifier
            data = call_args[1]["data"]
            assert data["code_verifier"] == server.code_verifier
            assert data["code"] == "test_code"
            assert data["client_id"] == server.client_id
            assert data["client_secret"] == server.client_secret

    @pytest.mark.asyncio
    async def test_exchange_code_for_token_failure(self):
        """Test failed token exchange."""
        server = OAuthLoginServer("google")

        # Mock failed response
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid grant"
        mock_response.json.side_effect = ValueError("Not JSON")

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with pytest.raises(Exception, match="Token exchange failed"):
                await server._exchange_code_for_token("test_code")

    @pytest.mark.asyncio
    async def test_get_user_info_google(self):
        """Test getting user info from Google."""
        server = OAuthLoginServer("google")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "email": "test@example.com",
            "name": "Test User",
            "picture": "https://example.com/photo.jpg",
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            user_info = await server._get_user_info("test_access_token")

            assert user_info["email"] == "test@example.com"

            # Verify request
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            assert call_args[0][0] == PROVIDERS["google"]["userinfo_url"]
            assert call_args[1]["headers"]["Authorization"] == "Bearer test_access_token"

    @pytest.mark.asyncio
    async def test_get_user_info_discord_fallback(self):
        """Test getting user info from Discord with fallback."""
        server = OAuthLoginServer("discord")

        # Mock failed response
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            # Discord should fall back to default email
            user_info = await server._get_user_info("test_access_token")

            assert user_info["email"] == "unknown@example.com"

    def test_save_auth_data(self):
        """Test saving auth data to file."""
        server = OAuthLoginServer("google")

        token_data = {
            "access_token": "test_access_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid email profile",
        }

        user_info = {
            "email": "test@example.com",
            "name": "Test User",
        }

        # Save auth data
        server._save_auth_data(token_data, user_info)

        # Check file exists
        assert self.auth_file.exists()

        # Check file permissions (approximate check)
        import stat

        mode = self.auth_file.stat().st_mode
        assert mode & stat.S_IRUSR  # User read
        assert mode & stat.S_IWUSR  # User write
        assert not mode & stat.S_IRGRP  # Group read not allowed
        assert not mode & stat.S_IWGRP  # Group write not allowed
        assert not mode & stat.S_IROTH  # Others read not allowed
        assert not mode & stat.S_IWOTH  # Others write not allowed

        # Check file content
        with open(self.auth_file, "r") as f:
            saved_data = json.load(f)

        assert saved_data["provider"] == "google"
        assert saved_data["access_token"] == "test_access_token"
        assert saved_data["token_type"] == "Bearer"
        assert saved_data["scope"] == "openid email profile"
        assert saved_data["user_email"] == "test@example.com"
        assert "expires_at_utc" in saved_data
        assert "obtained_at_utc" in saved_data

        # Verify directory permissions (should be 0700)
        dir_mode = self.auth_dir.stat().st_mode
        assert dir_mode & stat.S_IRUSR
        assert dir_mode & stat.S_IWUSR
        assert dir_mode & stat.S_IXUSR
        assert not dir_mode & stat.S_IRGRP
        assert not dir_mode & stat.S_IWGRP
        assert not dir_mode & stat.S_IXGRP
        assert not dir_mode & stat.S_IROTH
        assert not dir_mode & stat.S_IWOTH
        assert not dir_mode & stat.S_IXOTH


class TestFastAPIRoutes:
    """Test FastAPI routes."""

    def setup_method(self):
        """Setup for each test."""
        # Mock environment variables
        self.env_patcher = patch.dict(
            os.environ,
            {
                "OYSTER_GOOGLE_CLIENT_ID": "test-client-id",
                "OYSTER_GOOGLE_CLIENT_SECRET": "test-client-secret",
            },
        )
        self.env_patcher.start()

        # Create server
        self.server = OAuthLoginServer("google")
        self.client = TestClient(self.server.app)

        # Create temp directory for auth file
        self.temp_dir = tempfile.mkdtemp()
        self.auth_dir = Path(self.temp_dir) / ".oyster"
        self.auth_file = self.auth_dir / "auth.json"

        # Patch AUTH_DIR and AUTH_FILE
        self.auth_dir_patcher = patch("bin.oauth_login_server.AUTH_DIR", self.auth_dir)
        self.auth_file_patcher = patch("bin.oauth_login_server.AUTH_FILE", self.auth_file)
        self.auth_dir_patcher.start()
        self.auth_file_patcher.start()

    def teardown_method(self):
        """Cleanup after each test."""
        self.env_patcher.stop()
        self.auth_dir_patcher.stop()
        self.auth_file_patcher.stop()

        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_root_endpoint(self):
        """Test root endpoint."""
        response = self.client.get("/")
        assert response.status_code == 200
        assert response.json()["message"] == "Oyster OAuth login server for google"

    def test_callback_missing_code(self):
        """Test callback with missing code parameter."""
        response = self.client.get("/oauth/callback")
        assert response.status_code == 400
        assert "Missing authorization code" in response.text

    def test_callback_invalid_state(self):
        """Test callback with invalid state parameter."""
        response = self.client.get("/oauth/callback?code=abc123&state=wrong_state")
        assert response.status_code == 400
        assert "Invalid state parameter" in response.text

    def test_callback_with_error(self):
        """Test callback with OAuth error."""
        response = self.client.get("/oauth/callback?error=access_denied")
        assert response.status_code == 400
        assert "access_denied" in response.text

    @patch("bin.oauth_login_server.OAuthLoginServer._exchange_code_for_token")
    @patch("bin.oauth_login_server.OAuthLoginServer._get_user_info")
    def test_callback_success(self, mock_get_user_info, mock_exchange_token):
        """Test successful callback."""

        # Mock token exchange to return a coroutine
        async def mock_exchange(code):
            return {
                "access_token": "test_access_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "openid email profile",
            }

        # Mock user info to return a coroutine
        async def mock_get_user_info_func(token):
            return {"email": "test@example.com"}

        mock_exchange_token.side_effect = mock_exchange
        mock_get_user_info.side_effect = mock_get_user_info_func

        # Call with correct state
        response = self.client.get(f"/oauth/callback?code=abc123&state={self.server.state}")

        assert response.status_code == 200
        assert "✓ Logged in successfully!" in response.text
        assert "You can close this window" in response.text

        # Verify mocks were called
        mock_exchange_token.assert_called_once_with("abc123")
        mock_get_user_info.assert_called_once_with("test_access_token")

    @patch("bin.oauth_login_server.OAuthLoginServer._exchange_code_for_token")
    def test_callback_token_exchange_failure(self, mock_exchange_token):
        """Test callback when token exchange fails."""

        # Mock failed token exchange
        async def mock_exchange_failed(code):
            raise Exception("Token exchange failed")

        mock_exchange_token.side_effect = mock_exchange_failed

        response = self.client.get(f"/oauth/callback?code=abc123&state={self.server.state}")

        assert response.status_code == 500
        assert "Token exchange failed" in response.text


class TestCLI:
    """Test CLI interface."""

    def setup_method(self):
        """Setup for each test."""
        # Mock environment variables
        self.env_patcher = patch.dict(
            os.environ,
            {
                "OYSTER_GOOGLE_CLIENT_ID": "test-client-id",
                "OYSTER_GOOGLE_CLIENT_SECRET": "test-client-secret",
            },
        )
        self.env_patcher.start()

        # Create temp directory for auth file
        self.temp_dir = tempfile.mkdtemp()
        self.auth_dir = Path(self.temp_dir) / ".oyster"
        self.auth_file = self.auth_dir / "auth.json"

        # Patch AUTH_DIR and AUTH_FILE
        self.auth_dir_patcher = patch("bin.oauth_login_server.AUTH_DIR", self.auth_dir)
        self.auth_file_patcher = patch("bin.oauth_login_server.AUTH_FILE", self.auth_file)
        self.auth_dir_patcher.start()
        self.auth_file_patcher.start()

    def teardown_method(self):
        """Cleanup after each test."""
        self.env_patcher.stop()
        self.auth_dir_patcher.stop()
        self.auth_file_patcher.stop()

        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_cli_missing_env_vars(self):
        """Test CLI with missing environment variables."""
        from bin.oauth_login_server import main

        # Remove env vars
        os.environ.pop("OYSTER_GOOGLE_CLIENT_ID")

        import click.testing

        runner = click.testing.CliRunner()

        result = runner.invoke(main, ["--provider", "google"])

        assert result.exit_code == 1
        assert "Missing OYSTER_GOOGLE_CLIENT_ID" in result.output

    @patch.dict(os.environ, {"OYSTER_FAKE_PROVIDER": "1"})
    def test_cli_fake_provider_mode(self):
        """Test CLI in fake provider mode."""
        import click.testing

        from bin.oauth_login_server import main

        runner = click.testing.CliRunner()

        result = runner.invoke(main, ["--provider", "google"])

        assert result.exit_code == 0
        assert "Running in fake provider mode for testing" in result.output
        assert "Fake auth data saved to" in result.output

        # Check that auth file was created
        assert self.auth_file.exists()

        # Check file content
        with open(self.auth_file, "r") as f:
            auth_data = json.load(f)

        assert auth_data["provider"] == "google"
        assert auth_data["access_token"] == "fake_access_token"


class TestPKCE:
    """Test PKCE implementation."""

    def setup_method(self):
        """Setup for each test."""
        # Mock environment variables
        self.env_patcher = patch.dict(
            os.environ,
            {
                "OYSTER_GOOGLE_CLIENT_ID": "test-google-client-id",
                "OYSTER_GOOGLE_CLIENT_SECRET": "test-google-client-secret",
            },
        )
        self.env_patcher.start()

        # Create temp directory for auth file
        self.temp_dir = tempfile.mkdtemp()
        self.auth_dir = Path(self.temp_dir) / ".oyster"
        self.auth_file = self.auth_dir / "auth.json"

        # Patch AUTH_DIR and AUTH_FILE
        self.auth_dir_patcher = patch("bin.oauth_login_server.AUTH_DIR", self.auth_dir)
        self.auth_file_patcher = patch("bin.oauth_login_server.AUTH_FILE", self.auth_file)
        self.auth_dir_patcher.start()
        self.auth_file_patcher.start()

    def teardown_method(self):
        """Cleanup after each test."""
        self.env_patcher.stop()
        self.auth_dir_patcher.stop()
        self.auth_file_patcher.stop()

        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_pkce_flow_integration(self):
        """Test that PKCE code verifier and challenge are properly linked."""
        server = OAuthLoginServer("google")

        # Verify code verifier length (43-128 chars)
        # secrets.token_urlsafe(32) produces 43 chars
        assert 43 <= len(server.code_verifier) <= 128

        # Verify code challenge is base64url(SHA256(code_verifier))
        import base64
        import hashlib

        # Calculate expected challenge
        digest = hashlib.sha256(server.code_verifier.encode()).digest()
        expected_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")

        assert server.code_challenge == expected_challenge

        # Verify no padding in challenge
        assert "=" not in server.code_challenge

        # Verify auth URL includes challenge
        auth_url = server.get_auth_url()
        parsed = urlparse(auth_url)
        query_params = parse_qs(parsed.query)
        assert query_params["code_challenge"] == [server.code_challenge]
        assert query_params["code_challenge_method"] == ["S256"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
