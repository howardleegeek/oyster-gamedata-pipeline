#!/usr/bin/env python3
"""
OAuth login server for Google and Discord.
Starts a local FastAPI server to handle OAuth callbacks.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

# Module-level logger
logger = logging.getLogger(__name__)

import click
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

# OAuth provider configurations
PROVIDERS = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scope": "openid email profile",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
    },
    "discord": {
        "auth_url": "https://discord.com/api/oauth2/authorize",
        "token_url": "https://discord.com/api/oauth2/token",
        "scope": "identify email",
        "userinfo_url": "https://discord.com/api/v10/users/@me",
    },
}

# Default port for OAuth callback
DEFAULT_PORT = 18723
CALLBACK_PATH = "/oauth/callback"

# Auth file location
AUTH_DIR = Path.home() / ".oyster"
AUTH_FILE = AUTH_DIR / "auth.json"


class OAuthLoginServer:
    def __init__(self, provider: str, port: int = DEFAULT_PORT):
        self.provider = provider
        self.port = port
        self.config = PROVIDERS[provider]

        # Get client credentials from environment
        self.client_id = os.getenv(f"OYSTER_{provider.upper()}_CLIENT_ID")
        self.client_secret = os.getenv(f"OYSTER_{provider.upper()}_CLIENT_SECRET")

        if not self.client_id:
            raise ValueError(f"Missing OYSTER_{provider.upper()}_CLIENT_ID environment variable")
        if not self.client_secret:
            raise ValueError(
                f"Missing OYSTER_{provider.upper()}_CLIENT_SECRET environment variable"
            )

        # PKCE code verifier and challenge
        self.code_verifier = secrets.token_urlsafe(32)
        self.code_challenge = self._generate_code_challenge(self.code_verifier)

        # State for CSRF protection
        self.state = secrets.token_urlsafe(16)

        # Store token exchange result
        self.token_result: Optional[Dict] = None
        self.shutdown_event = asyncio.Event()

        # FastAPI app
        self.app = FastAPI(title=f"Oyster OAuth Login - {provider}")
        self._setup_routes()

    def _generate_code_challenge(self, code_verifier: str) -> str:
        """Generate PKCE code challenge from verifier."""
        # SHA256 hash of code verifier
        digest = hashlib.sha256(code_verifier.encode()).digest()
        # Base64 URL-safe encode without padding
        challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return challenge

    def _setup_routes(self):
        """Setup FastAPI routes."""

        @self.app.get("/")
        async def root():
            return {"message": f"Oyster OAuth login server for {self.provider}"}

        @self.app.get(CALLBACK_PATH)
        async def oauth_callback(
            request: Request,
            code: Optional[str] = None,
            state: Optional[str] = None,
            error: Optional[str] = None,
        ):
            """Handle OAuth callback."""
            if error:
                return HTMLResponse(f"<h1>Authorization Error</h1><p>{error}</p>", status_code=400)

            if not code:
                return HTMLResponse("<h1>Missing authorization code</h1>", status_code=400)

            # Verify state matches
            if state != self.state:
                return HTMLResponse("<h1>Invalid state parameter</h1>", status_code=400)

            try:
                # Exchange code for token
                token_data = await self._exchange_code_for_token(code)

                # Get user info
                user_info = await self._get_user_info(token_data["access_token"])

                # Save auth data
                self._save_auth_data(token_data, user_info)

                # Schedule shutdown
                asyncio.create_task(self._schedule_shutdown())

                return HTMLResponse("""
                    <html>
                        <head><title>Oyster Login Successful</title></head>
                        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                            <h1 style="color: green;">✓ Logged in successfully!</h1>
                            <p>You can close this window.</p>
                            <p>Token saved to ~/.oyster/auth.json</p>
                        </body>
                    </html>
                """)

            except Exception as e:
                return HTMLResponse(f"<h1>Error</h1><p>{str(e)}</p>", status_code=500)

    async def _exchange_code_for_token(self, code: str) -> Dict:
        """Exchange authorization code for access token."""
        token_url = self.config["token_url"]

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"http://localhost:{self.port}{CALLBACK_PATH}",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        # Add PKCE code verifier
        data["code_verifier"] = self.code_verifier

        async with httpx.AsyncClient() as client:
            # Discord expects data as form-encoded, Google too
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            response = await client.post(token_url, data=data, headers=headers)

            if response.status_code != 200:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get("error_description", error_detail)
                except (ValueError, KeyError) as exc:
                    logger.debug("Failed to parse OAuth error JSON: %s", exc)
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Token exchange failed: {error_detail}",
                )

            return response.json()

    async def _get_user_info(self, access_token: str) -> Dict:
        """Get user info from provider."""
        userinfo_url = self.config["userinfo_url"]

        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient() as client:
            response = await client.get(userinfo_url, headers=headers)

            if response.status_code != 200:
                # For Discord, we might need to handle different response format
                if self.provider == "discord":
                    # Try without userinfo for Discord if endpoint fails
                    return {"email": "unknown@example.com"}
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Failed to get user info: {response.text}",
                )

            return response.json()

    def _save_auth_data(self, token_data: Dict, user_info: Dict):
        """Save authentication data to ~/.oyster/auth.json."""
        # Create auth directory if it doesn't exist
        AUTH_DIR.mkdir(mode=0o700, exist_ok=True)

        # Prepare auth data
        auth_data = {
            "provider": self.provider,
            "access_token": token_data["access_token"],
            "token_type": token_data.get("token_type", "Bearer"),
            "expires_at_utc": self._calculate_expiry(token_data),
            "scope": token_data.get("scope", self.config["scope"]),
            "user_email": user_info.get("email", ""),
            "obtained_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        # Write to file with mode 0600
        with open(AUTH_FILE, "w") as f:
            json.dump(auth_data, f, indent=2)

        # Set file permissions to 0600
        AUTH_FILE.chmod(0o600)

        self.token_result = auth_data

    def _calculate_expiry(self, token_data: Dict) -> str:
        """Calculate token expiry time."""
        expires_in = token_data.get("expires_in", 3600)  # Default 1 hour
        expiry_time = datetime.now(timezone.utc).timestamp() + expires_in
        return datetime.fromtimestamp(expiry_time, timezone.utc).isoformat()

    async def _schedule_shutdown(self):
        """Schedule server shutdown after 5 seconds."""
        await asyncio.sleep(5)
        self.shutdown_event.set()

    def get_auth_url(self) -> str:
        """Generate OAuth authorization URL."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": f"http://localhost:{self.port}{CALLBACK_PATH}",
            "response_type": "code",
            "scope": self.config["scope"],
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
        }

        # Build URL with query parameters
        from urllib.parse import urlencode

        query_string = urlencode(params)
        return f"{self.config['auth_url']}?{query_string}"

    async def run(self):
        """Run the OAuth login server."""
        # Generate and open auth URL
        auth_url = self.get_auth_url()
        print(f"Opening browser for {self.provider} OAuth login...")
        print(f"If browser doesn't open, visit: {auth_url}")

        # Try to open browser
        try:
            webbrowser.open(auth_url)
        except Exception as e:
            print(f"Warning: Could not open browser: {e}")
            print(f"Please manually visit: {auth_url}")

        # Start server
        config = uvicorn.Config(self.app, host="localhost", port=self.port, log_level="warning")
        server = uvicorn.Server(config)

        # Run server until shutdown event
        await server.serve()

        # Wait for shutdown event
        await self.shutdown_event.wait()

        # Stop the server
        server.should_exit = True


@click.command()
@click.option(
    "--provider",
    type=click.Choice(["google", "discord"]),
    required=True,
    help="OAuth provider (google or discord)",
)
@click.option(
    "--port",
    type=int,
    default=DEFAULT_PORT,
    help=f"Port for callback server (default: {DEFAULT_PORT})",
)
def main(provider: str, port: int):
    """Oyster OAuth Login Server

    Starts a local server to handle OAuth callbacks from Google or Discord.
    After successful authentication, saves token to ~/.oyster/auth.json.
    """
    try:
        # Check for fake provider mode (for testing)
        if os.getenv("OYSTER_FAKE_PROVIDER") == "1":
            print("Running in fake provider mode for testing...")
            # In fake mode, we'll create a mock auth file
            AUTH_DIR.mkdir(mode=0o700, exist_ok=True)
            auth_data = {
                "provider": provider,
                "access_token": "fake_access_token",
                "token_type": "Bearer",
                "expires_at_utc": datetime.now(timezone.utc).isoformat(),
                "scope": PROVIDERS[provider]["scope"],
                "user_email": "test@example.com",
                "obtained_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            with open(AUTH_FILE, "w") as f:
                json.dump(auth_data, f, indent=2)
            AUTH_FILE.chmod(0o600)
            print(f"Fake auth data saved to {AUTH_FILE}")
            return

        # Create and run server
        server = OAuthLoginServer(provider, port)

        # Run async server
        asyncio.run(server.run())

        if server.token_result:
            print(f"\n✓ Successfully logged in with {provider}")
            print(f"Token saved to {AUTH_FILE}")
        else:
            print("\n⚠ Login process completed but no token was saved")
            sys.exit(1)

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(
            f"Please set OYSTER_{provider.upper()}_CLIENT_ID and "
            f"OYSTER_{provider.upper()}_CLIENT_SECRET environment variables",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
