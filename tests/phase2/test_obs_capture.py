"""Tests for obs_capture.py."""

import base64
import hashlib
import os
import sys

# Ensure obs_capture.py is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import obs_capture


def test_module_imports():
    """Import succeeds."""
    import obs_capture  # noqa: F811

    assert hasattr(obs_capture, "OBSSpectatorCapture")


def test_OBSSpectatorCapture_default_init():
    """Default init: host=localhost, port=4455, password empty."""
    cap = obs_capture.OBSSpectatorCapture()
    assert cap.host == "localhost"
    assert cap.port == 4455
    assert cap.password == ""


def test_OBSSpectatorCapture_custom_init():
    """Custom init: pass custom values, attributes set."""
    cap = obs_capture.OBSSpectatorCapture(host="192.168.1.10", port=5555, password="secret")
    assert cap.host == "192.168.1.10"
    assert cap.port == 5555
    assert cap.password == "secret"


def test_obs_websocket_v5_auth_hash_format():
    """When password set, _auth_hash produces correct OBS WebSocket v5 auth string.
    
    OBS WebSocket v5 auth: base64(SHA256(base64(SHA256(password + salt)) + challenge))
    """
    cap = obs_capture.OBSSpectatorCapture(password="mypassword")
    challenge = "mock_challenge_456"
    salt = "mock_salt_123"
    
    # Calculate expected using the OBS WebSocket v5 protocol:
    # 1. SHA256(password + salt) -> base64
    # 2. SHA256(base64_result + challenge) -> base64
    secret = hashlib.sha256(("mypassword" + salt).encode()).digest()
    secret_b64 = base64.b64encode(secret).decode()
    expected = base64.b64encode(
        hashlib.sha256((secret_b64 + challenge).encode()).digest()
    ).decode()
    
    result = cap._auth_hash(challenge, salt)
    assert result == expected


def test_auth_hash_returns_empty_string_when_no_password():
    """When password is empty, _auth_hash returns empty string."""
    cap = obs_capture.OBSSpectatorCapture(password="")
    result = cap._auth_hash("challenge", "salt")
    assert result == ""


def test_connect_is_async_coroutine():
    """connect() is an async method returning a coroutine."""
    import asyncio
    cap = obs_capture.OBSSpectatorCapture()
    # connect() is async, so calling it returns a coroutine
    coro = cap.connect()
    assert asyncio.iscoroutine(coro)
    # Close the coroutine to avoid RuntimeWarning
    coro.close()