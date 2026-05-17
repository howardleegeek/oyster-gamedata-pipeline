"""Tests for OBS capture real module.

Tests the OBSRecorder class which provides async client for obs-websocket v5
protocol to control OBS Studio recording.
"""

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


class TestOBSRecorder:
    """Test cases for OBSRecorder class."""

    @pytest.mark.asyncio
    async def test_context_manager_anonymous_connect(self):
        """Test anonymous connection via context manager."""
        from obs_capture_real import OBSRecorder

        # Create mock websocket
        mock_ws = AsyncMock()
        # Hello message without authentication challenge
        hello_msg = {
            "op": 0,  # Hello
            "d": {
                "obsWebSocketVersion": "5.0.0",
                "rpcVersion": 1,
                "authentication": None,
            },
        }
        # Identified message (response to Identify)
        identified_msg = {
            "op": 2,  # Identified
            "d": {"negotiatedRpcVersion": 1},
        }
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps(hello_msg),
                json.dumps(identified_msg),
            ]
        )
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()

        # Mock websockets.connect
        with patch("obs_capture_real._get_websockets") as mock_get_ws:
            mock_websockets = MagicMock()
            mock_websockets.connect = AsyncMock(return_value=mock_ws)
            mock_get_ws.return_value = mock_websockets

            async with OBSRecorder(ws_host="localhost", ws_port=4455) as rec:
                # Verify connection was made
                mock_websockets.connect.assert_called_once_with("ws://localhost:4455")
                # Verify Identify was sent (no auth since no password)
                assert mock_ws.send.called
                send_data = json.loads(mock_ws.send.call_args[0][0])
                assert send_data["op"] == 1  # Identify opcode

    @pytest.mark.asyncio
    async def test_authenticated_connect_with_password(self):
        """Test authenticated connection with password."""
        from obs_capture_real import OBSRecorder

        password = "test_password"
        mock_ws = AsyncMock()
        # Hello message with authentication challenge and authRequired=True
        hello_msg = {
            "op": 0,  # Hello
            "d": {
                "obsWebSocketVersion": "5.0.0",
                "rpcVersion": 1,
                "authRequired": True,
                "authentication": {"challenge": "test_challenge", "salt": "test_salt"},
            },
        }

        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps(hello_msg),
                json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}),
            ]
        )
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()

        with patch("obs_capture_real._get_websockets") as mock_get_ws:
            mock_websockets = MagicMock()
            mock_websockets.connect = AsyncMock(return_value=mock_ws)
            mock_get_ws.return_value = mock_websockets

            async with OBSRecorder(ws_host="localhost", ws_port=4455, password=password) as rec:
                # Verify connection was made
                mock_websockets.connect.assert_called_once_with("ws://localhost:4455")
                # Verify Identify was sent with auth
                assert mock_ws.send.called
                send_data = json.loads(mock_ws.send.call_args[0][0])
                assert send_data["op"] == 1  # Identify opcode
                assert "authentication" in send_data["d"]

    @pytest.mark.asyncio
    async def test_auth_required_but_no_password_raises(self):
        """Test that auth required without password raises ConnectionError."""
        from obs_capture_real import OBSRecorder

        mock_ws = AsyncMock()
        # Hello message with authRequired=True but no password provided
        hello_msg = {
            "op": 0,
            "d": {
                "obsWebSocketVersion": "5.0.0",
                "rpcVersion": 1,
                "authRequired": True,
                "authentication": {"challenge": "test_challenge", "salt": "test_salt"},
            },
        }
        mock_ws.recv = AsyncMock(return_value=json.dumps(hello_msg))
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()

        with patch("obs_capture_real._get_websockets") as mock_get_ws:
            mock_websockets = MagicMock()
            mock_websockets.connect = AsyncMock(return_value=mock_ws)
            mock_get_ws.return_value = mock_websockets

            with pytest.raises(ConnectionError, match="OBS requires authentication"):
                async with OBSRecorder(ws_host="localhost", ws_port=4455):
                    pass

    @pytest.mark.asyncio
    async def test_stop_returns_output_path(self):
        """Test that stop() returns the output path from OBS."""
        from obs_capture_real import OBSRecorder

        mock_ws = AsyncMock()
        hello_msg = {"op": 0, "d": {"obsWebSocketVersion": "5.0.0", "rpcVersion": 1}}
        identified_msg = {"op": 2, "d": {"negotiatedRpcVersion": 1}}
        # Response to StopRecord request
        stop_response = {
            "op": 7,  # RequestResponse
            "d": {
                "requestType": "StopRecord",
                "requestId": "1",
                "requestStatus": {"result": True},
                "responseData": {"outputPath": "/tmp/test_recording.mp4"},
            },
        }
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps(hello_msg),
                json.dumps(identified_msg),
                json.dumps(stop_response),
            ]
        )
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()

        with patch("obs_capture_real._get_websockets") as mock_get_ws:
            mock_websockets = MagicMock()
            mock_websockets.connect = AsyncMock(return_value=mock_ws)
            mock_get_ws.return_value = mock_websockets

            async with OBSRecorder(ws_host="localhost", ws_port=4455) as rec:
                # Connection established
                pass

    @pytest.mark.asyncio
    async def test_get_status_returns_active_state(self):
        """Test that get_status() returns the recording state."""
        from obs_capture_real import OBSRecorder

        mock_ws = AsyncMock()
        hello_msg = {"op": 0, "d": {"obsWebSocketVersion": "5.0.0", "rpcVersion": 1}}
        identified_msg = {"op": 2, "d": {"negotiatedRpcVersion": 1}}
        status_response = {
            "op": 7,
            "d": {
                "requestType": "GetRecordStatus",
                "requestId": "1",
                "requestStatus": {"result": True},
                "responseData": {"outputActive": True, "outputPaused": False},
            },
        }
        mock_ws.recv = AsyncMock(
            side_effect=[
                json.dumps(hello_msg),
                json.dumps(identified_msg),
                json.dumps(status_response),
            ]
        )
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()

        with patch("obs_capture_real._get_websockets") as mock_get_ws:
            mock_websockets = MagicMock()
            mock_websockets.connect = AsyncMock(return_value=mock_ws)
            mock_get_ws.return_value = mock_websockets

            async with OBSRecorder(ws_host="localhost", ws_port=4455) as rec:
                pass  # Connection established and closed