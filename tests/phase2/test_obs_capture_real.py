"""Tests for OBS capture real module."""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestOBSCaptureReal:
    """Test cases for OBSRecorder class."""

    @pytest.mark.asyncio
    async def test_init_sets_defaults(self):
        """Test OBSRecorder initializes with correct defaults."""
        from obs_capture_real import OBSRecorder

        obs = OBSRecorder(ws_host="localhost", ws_port=4455, password="secret")

        assert obs._ws_host == "localhost"
        assert obs._ws_port == 4455
        assert obs._password == "secret"
        assert obs._ws is None
        assert obs._msg_id == 0

    @pytest.mark.asyncio
    async def test_uri_property(self):
        """Test _uri property returns correct websocket URI."""
        from obs_capture_real import OBSRecorder

        obs = OBSRecorder(ws_host="192.168.1.100", ws_port=5555)

        assert obs._uri == "ws://192.168.1.100:5555"

    @pytest.mark.asyncio
    async def test_next_id_increments(self):
        """Test _next_id returns incrementing IDs."""
        from obs_capture_real import OBSRecorder

        obs = OBSRecorder()

        assert obs._next_id() == 1
        assert obs._next_id() == 2
        assert obs._next_id() == 3

    @pytest.mark.asyncio
    async def test_start_sets_recording_flag(self):
        """Test start method sets _recording flag."""
        from obs_capture_real import OBSRecorder

        obs = OBSRecorder()
        obs._identified = True
        obs._ws = AsyncMock()  # Mock websocket

        await obs.start("/tmp/clip.mp4")

        assert obs._recording is True

    @pytest.mark.asyncio
    async def test_stop_clears_recording_flag(self):
        """Test stop method clears _recording flag."""
        from obs_capture_real import OBSRecorder

        obs = OBSRecorder()
        obs._identified = True
        obs._recording = True
        obs._ws = AsyncMock()  # Mock websocket

        await obs.stop()

        assert obs._recording is False

    @pytest.mark.asyncio
    async def test_get_status_returns_dict(self):
        """Test get_status returns status dict."""
        from obs_capture_real import OBSRecorder

        obs = OBSRecorder()
        obs._identified = True
        obs._recording = True
        obs._ws = AsyncMock()

        status = await obs.get_status()

        assert isinstance(status, dict)
        assert "recording" in status

    @pytest.mark.asyncio
    async def test_context_manager_connects_and_authenticates(self):
        """Test context manager connects and authenticates."""
        from obs_capture_real import OBSRecorder

        obs = OBSRecorder(ws_host="localhost", ws_port=4455)

        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "op": 2,  # Identified (no auth needed)
                    "d": {"rpcVersion": 1},
                }
            )
        )

        with patch("obs_capture_real._get_websockets") as mock_get_ws:
            mock_get_ws.return_value.connect = AsyncMock(return_value=mock_ws)

            async with obs:
                assert obs._ws is not None
                assert obs._identified is True
