"""Tests for obs_capture.py."""

import base64
import hashlib
import os
import sys

import pytest

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


def test_obs_websocket_v5_auth_challenge_format():
    """When password set, helper produces correct base64+SHA256 (mock challenge salt)."""
    cap = obs_capture.OBSSpectatorCapture(password="mypassword")
    challenge = "mock_challenge_456"
    salt = "mock_salt_123"
    # _auth_hash: base64(SHA256(base64(SHA256(password + salt)) + challenge))
    secret = hashlib.sha256(("mypassword" + salt).encode()).digest()
    secret_b64 = base64.b64encode(secret).decode()
    expected = base64.b64encode(
        hashlib.sha256((secret_b64 + challenge).encode("utf-8")).digest()
    ).decode("ascii")
    result = cap._auth_hash(challenge, salt)
    assert result == expected


@pytest.mark.asyncio
async def test_connect_returns_false_when_websocket_lib_missing(monkeypatch):
    """Monkey-patch importlib so websocket-client import fails, assert connect() returns False."""
    import importlib

    original_import_module = importlib.import_module

    def fake_import_module(name, *args, **kwargs):
        if name == "websocket":
            raise ImportError("No module named 'websocket'")
        return original_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    cap = obs_capture.OBSSpectatorCapture()
    result = await cap.connect()
    assert result is False
