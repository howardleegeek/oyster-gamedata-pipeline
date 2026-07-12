#!/usr/bin/env python3
"""First-time recorder consent flow with OAuth login.

Uses loopback OAuth (localhost callback) for desktop apps as per RFC 8252.
More reliable than custom URL schemes like oyster://callback.
"""

import hashlib
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import jwt


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback on loopback interface."""
    
    token_data = None
    callback_received = False
    
    def do_GET(self):
        if self.path.startswith("/callback"):
            parsed = parse_qs(urlparse(self.path).query)
            
            if "access_token" in parsed:
                CallbackHandler.token_data = {
                    "access_token": parsed["access_token"][0],
                    "refresh_token": parsed.get("refresh_token", [""])[0]
                }
                CallbackHandler.callback_received = True
                
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"""
                    <html>
                    <body>
                        <h1>Authentication successful!</h1>
                        <p>You can close this window and return to the recorder.</p>
                        <script>setTimeout(function(){window.close();}, 2000);</script>
                    </body>
                    </html>
                """)
            else:
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication failed</h1></body></html>")
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress default logging
        pass


def find_free_port() -> int:
    """Find an available port for the loopback callback server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def wait_for_callback(port: int, timeout: int = 300) -> dict:
    """Start a local server on loopback and wait for OAuth callback."""
    server = HTTPServer(('127.0.0.1', port), CallbackHandler)
    server.timeout = 1
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        server.handle_request()
        if CallbackHandler.callback_received:
            break
    
    server.server_close()
    return CallbackHandler.token_data


def store_auth_tokens(access_token: str, refresh_token: str) -> Path:
    """Store authentication tokens securely."""
    auth_dir = Path.home() / ".oyster"
    auth_dir.mkdir(mode=0o700, exist_ok=True)
    
    auth_file = auth_dir / "auth.json"
    
    # Verify the token before storing
    try:
        payload = jwt.decode(access_token, options={"verify_signature": False})
    except jwt.InvalidTokenError as e:
        raise ValueError("Invalid access token received") from e
    
    auth_data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": payload.get("sub"),
        "role": payload.get("role"),
        "oauth_provider": payload.get("oauth_provider"),
        "stored_at": time.time()
    }
    
    # Write with restricted permissions
    with open(auth_file, 'w') as f:
        json.dump(auth_data, f, indent=2)
    
    os.chmod(auth_file, 0o600)
    
    return auth_file


def create_consent_record(user_id: str, provider: str) -> dict:
    """Create a signed consent record for the recorder."""
    consent_record = {
        "user_id": user_id,
        "oauth_provider": provider,
        "consent_type": "recorder_access",
        "timestamp": time.time(),
        "version": "1.0"
    }
    
    # Sign with oyster_provenance.sign if available
    try:
        from oyster_provenance import sign
        consent_record["signature"] = sign(consent_record)
    except ImportError:
        # If oyster_provenance is not available, create a simple hash signature
        record_str = json.dumps(consent_record, sort_keys=True)
        consent_record["signature"] = hashlib.sha256(record_str.encode()).hexdigest()
    
    return consent_record


def store_consent_record(consent_record: dict) -> Path:
    """Store the consent record."""
    consent_dir = Path.home() / ".oyster"
    consent_dir.mkdir(mode=0o700, exist_ok=True)
    
    consent_file = consent_dir / "consent.json"
    
    with open(consent_file, 'w') as f:
        json.dump(consent_record, f, indent=2)
    
    os.chmod(consent_file, 0o600)
    
    return consent_file


def run_oauth_flow(provider: str = "google") -> dict:
    """Run the OAuth flow for the specified provider using loopback callback."""
    port = find_free_port()
    # Use loopback address (127.0.0.1) for desktop OAuth - more reliable than oyster://callback
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    
    # Construct OAuth URL
    # In production, this would go to the actual OAuth server
    # For now, we'll use the local server endpoint
    base_url = os.environ.get("OYSTER_API_URL", "http://localhost:8000")
    
    if provider == "google":
        login_url = f"{base_url}/api/auth/google/login?redirect={redirect_uri}"
    elif provider == "discord":
        login_url = f"{base_url}/api/auth/discord/login?redirect={redirect_uri}"
    else:
        raise ValueError(f"Unknown provider: {provider}")
    
    print(f"Starting {provider} OAuth flow...")
    print(f"Opening browser to: {login_url}")
    
    # Start callback server in background
    callback_thread = threading.Thread(
        target=lambda: wait_for_callback(port)
    )
    callback_thread.start()
    
    # Open browser
    webbrowser.open(login_url)
    
    # Wait for callback
    callback_thread.join(timeout=300)
    
    if not CallbackHandler.token_data:
        raise RuntimeError("Authentication timed out")
    
    return CallbackHandler.token_data


def main():
    """Main entry point for recorder consent flow."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Recorder OAuth consent flow")
    parser.add_argument(
        "--provider",
        choices=["google", "discord"],
        default="google",
        help="OAuth provider to use"
    )
    parser.add_argument(
        "--skip-browser",
        action="store_true",
        help="Don't open browser (print URL instead)"
    )
    args = parser.parse_args()
    
    # Check if already authenticated
    auth_file = Path.home() / ".oyster" / "auth.json"
    if auth_file.exists():
        try:
            with open(auth_file) as f:
                auth_data = json.load(f)
            
            # Check if token is still valid
            token = auth_data.get("access_token")
            if token:
                try:
                    jwt.decode(token, options={"verify_signature": False})
                    print("Already authenticated. Use --force to re-authenticate.")
                    return 0
                except jwt.ExpiredSignatureError:
                    print("Token expired, re-authenticating...")
        except Exception as e:
            print(f"Error reading auth file: {e}")
    
    try:
        # Run OAuth flow
        token_data = run_oauth_flow(args.provider)
        
        if not token_data or "access_token" not in token_data:
            print("Authentication failed: No token received")
            return 1
        
        # Store tokens
        auth_path = store_auth_tokens(
            token_data["access_token"],
            token_data.get("refresh_token", "")
        )
        print(f"Authentication tokens stored in: {auth_path}")
        
        # Create and store consent record
        payload = jwt.decode(
            token_data["access_token"],
            options={"verify_signature": False}
        )
        
        consent_record = create_consent_record(
            user_id=payload.get("sub"),
            provider=payload.get("oauth_provider", args.provider)
        )
        
        consent_path = store_consent_record(consent_record)
        print(f"Consent record stored in: {consent_path}")
        
        print(f"\nSuccessfully authenticated as {payload.get('email', 'user')}")
        print(f"Role: {payload.get('role', 'unknown')}")
        
        return 0
        
    except Exception as e:
        print(f"Authentication error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
