"""Tests for OBS capture real module."""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestOBSCaptureReal:
    """Test cases for OBSCaptureReal class."""

    @pytest.mark.asyncio
    async def test_anonymous_connect(self):
        """Test anonymous connection to OBS WebSocket."""
        from obs_capture_real import OBSCaptureReal

        obs = OBSCaptureReal(host="localhost", port=4455)

        # Create mock websocket
        mock_ws = AsyncMock()
        # Hello message without authentication challenge
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "op": 0,  # Hello
                    "d": {"obsWebSocketVersion": "5.0.0", "rpcVersion": 1, "authentication": None},
                }
            )
        )
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()

        # Mock websockets.connect
        with patch("websockets.connect", AsyncMock(return_value=mock_ws)) as mock_connect:
            result = await obs.connect(authenticate=False)

            # Verify connection was made
            mock_connect.assert_called_once_with("ws://localhost:4455")
            # Verify Identify was sent
            assert mock_ws.send.called
            # Get the last call (Identify message, not Hello)
            send_data = json.loads(mock_ws.send.call_args[0][0])
            assert send_data["op"] == 1  # Identify opcode
            assert result is True

    @pytest.mark.asyncio
    async def test_authenticated_connect_challenge(self):
        """Test authenticated connection with challenge-response."""
        from obs_capture_real import OBSCaptureReal

        password = "test_password"
        obs = OBSCaptureReal(host="localhost", port=4455, password=password)

        # Create mock websocket
        mock_ws = AsyncMock()
        # Hello message with authentication challenge
        hello_msg = {
            "op": 0,  # Hello
            "d": {
                "obsWebSocketVersion": "5.0.0",
                "rpcVersion": 1,
                "authentication": {"challenge": "test_challenge", "salt": "test_salt"},
            },
        }

        mock_ws.recv = AsyncMock(return_value=json.dumps(hello_msg))
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()

        with patch("websockets.connect", AsyncMock(return_value=mock_ws)):
            result = await obs.connect(authenticate=True)
            # First connect returns False indicating auth is needed
            # because the Hello message has authentication challenge
            assert result is False

            # Now authenticate
            auth_result = await obs.authenticate("test_salt", "test_challenge")
            assert auth_result is True
            # Verify Identify with auth was sent
            assert mock_ws.send.call_count >= 1

    @pytest.mark.asyncio
    async def test_start_record_opcode_6(self):
        """Test start recording sends opcode 6 (Request)."""
        from obs_capture_real import OBSCaptureReal

        obs = OBSCaptureReal(host="localhost", port=4455)
        obs._identified = True  # Pretend we're connected

        # Create mock websocket
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "op": 7,  # RequestResponse
                    "d": {
                        "requestType": "StartRecord",
                        "requestId": "start_record_1",
                        "requestStatus": {"result": True, "code": 100},
                    },
                }
            )
        )
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()
        obs._ws = mock_ws

        result = await obs.start_record()

        # Verify opcode 6 was sent
        send_data = json.loads(mock_ws.send.call_args[0][0])
        assert send_data["op"] == 6  # Request opcode
        assert send_data["d"]["requestType"] == "StartRecord"
        assert result["op"] == 7  # Response

    @pytest.mark.asyncio
    async def test_stop_record_opcode_6(self):
        """Test stop recording sends opcode 6 (Request)."""
        from obs_capture_real import OBSCaptureReal

        obs = OBSCaptureReal(host="localhost", port=4455)
        obs._identified = True
        obs._recording = True

        # Create mock websocket
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(
            return_value=json.dumps(
                {
                    "op": 7,  # RequestResponse
                    "d": {
                        "requestType": "StopRecord",
                        "requestId": "stop_record_1",
                        "requestStatus": {"result": True, "code": 100},
                        "responseData": {"outputPath": "/path/to/video.mp4"},
                    },
                }
            )
        )
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()
        obs._ws = mock_ws

        await obs.stop_record()

        # Verify opcode 6 was sent
        send_data = json.loads(mock_ws.send.call_args[0][0])
        assert send_data["op"] == 6  # Request opcode
        assert send_data["d"]["requestType"] == "StopRecord"
        assert obs._recording is False

    @pytest.mark.asyncio
    async def test_record_spectator_clip_missing_obs(self):
        """Test record spectator clip fails when OBS not connected."""
        from obs_capture_real import OBSCaptureError, OBSCaptureReal

        obs = OBSCaptureReal(host="localhost", port=4455)
        # Not connected - _identified is False

        with pytest.raises(OBSCaptureError, match="Not connected"):
            await obs.record_spectator_clip(duration=5.0)
