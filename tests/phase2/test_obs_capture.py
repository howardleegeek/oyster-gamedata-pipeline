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
    """When password set, helper produces correct OBS-WebSocket v5 auth hash.

    Reference algorithm (per obs-websocket protocol spec):
        secret = base64(SHA256(password + salt))
        auth   = base64(SHA256(secret + challenge))

    The shipped helper is ``OBSSpectatorCapture._auth_hash(challenge, salt)``;
    the older test signature used ``_auth_challenge_response(salt)`` which only
    matched a half-step of the algorithm — verify the real two-step hash here.
    """
    cap = obs_capture.OBSSpectatorCapture(password="mypassword")
    salt = "mock_salt_123"
    challenge = "mock_challenge_456"
    secret = base64.b64encode(
        hashlib.sha256(("mypassword" + salt).encode("utf-8")).digest()
    ).decode("ascii")
    expected = base64.b64encode(
        hashlib.sha256((secret + challenge).encode("utf-8")).digest()
    ).decode("ascii")
    result = cap._auth_hash(challenge, salt)
    assert result == expected


def test_connect_returns_false_when_websocket_lib_missing(monkeypatch):
    """Connect surfaces a False return value when the websockets lib is absent.

    The shipped ``connect()`` is an ``async def`` (the implementation is built
    on ``websockets`` which is async-only). Running it inside ``asyncio.run``
    keeps the test sync-shaped while honoring the real coroutine contract.
    """
    import asyncio

    # ``_import_websockets`` caches the resolved module on the module global.
    # Reset it so the patched import path is exercised on this run.
    monkeypatch.setattr(obs_capture, "_websockets", None, raising=False)

    def fake_import_websockets():
        raise ImportError("websockets library not installed (simulated)")

    monkeypatch.setattr(obs_capture, "_import_websockets", fake_import_websockets)

    cap = obs_capture.OBSSpectatorCapture()
    assert asyncio.run(cap.connect()) is False
