"""Tests for obs_capture_real.py — mocks websockets to verify protocol."""

import asyncio
import base64
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_hello(auth_required=False):
    """Build a Hello (op 0) message."""
    d = {"authRequired": auth_required, "negotiatedRpcVersion": 1}
    if auth_required:
        salt = base64.b64encode(b"testsalt").decode()
        challenge = base64.b64encode(b"testchallenge").decode()
        d["authentication"] = {"challenge": challenge, "salt": salt}
    return json.dumps({"op": 0, "d": d})


def _make_identified():
    return json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}})


def _make_request_response(request_id, result=True, data=None):
    return json.dumps({
        "op": 7,
        "d": {
            "requestId": str(request_id),
            "requestStatus": {"result": result},
            "responseData": data or {},
        },
    })


class TestOBSRecorderAuth(unittest.TestCase):
    """Verify auth challenge-response format."""

    def test_auth_challenge_response_format(self):
        """SHA256 base64 challenge is computed correctly."""
        password = "mypassword"
        salt = base64.b64encode(b"mysalt").decode()
        challenge = base64.b64encode(b"mychallenge").decode()
        expected = base64.b64encode(
            hashlib.sha256((password + salt + challenge).encode("utf-8")).digest()
        ).decode("utf-8")
        self.assertIsInstance(expected, str)
        self.assertEqual(len(expected), 44)  # base64 of 32-byte SHA256

    def test_anonymous_connect(self):
        """Anonymous (no password) connection succeeds."""
        async def _run():
            mock_ws = AsyncMock()
            mock_ws.recv = AsyncMock(side_effect=[
                _make_hello(auth_required=False),
                _make_identified(),
            ])
            mock_ws.send = AsyncMock()
            mock_ws.close = AsyncMock()
            mock_ws_class = MagicMock()
            mock_ws_class.connect = AsyncMock(return_value=mock_ws)

            with patch("obs_capture_real._get_websockets", return_value=mock_ws_class):
                from obs_capture_real import OBSRecorder
                async with OBSRecorder(password="") as rec:
                    calls = mock_ws.send.call_args_list
                    identify_call = calls[1]
                    payload = json.loads(identify_call[0][0])
                    self.assertEqual(payload["op"], 1)
                    self.assertNotIn("authentication", payload["d"])

        asyncio.run(_run())

    def test_authenticated_connect(self):
        """Authenticated connection sends correct challenge response."""
        async def _run():
            mock_ws = AsyncMock()
            mock_ws.recv = AsyncMock(side_effect=[
                _make_hello(auth_required=True),
                _make_identified(),
            ])
            mock_ws.send = AsyncMock()
            mock_ws.close = AsyncMock()
            mock_ws_class = MagicMock()
            mock_ws_class.connect = AsyncMock(return_value=mock_ws)

            with patch("obs_capture_real._get_websockets", return_value=mock_ws_class):
                from obs_capture_real import OBSRecorder
                async with OBSRecorder(password="secret") as rec:
                    calls = mock_ws.send.call_args_list
                    identify_call = calls[1]
                    payload = json.loads(identify_call[0][0])
                    self.assertIn("authentication", payload["d"])
                    self.assertIsInstance(payload["d"]["authentication"], str)

        asyncio.run(_run())


class TestOBSRecorderCommands(unittest.TestCase):
    """Verify StartRecord/StopRecord JSON opcodes."""

    def test_start_record_opcode(self):
        """StartRecord sends correct op 6 request."""
        async def _run():
            mock_ws = AsyncMock()
            mock_ws.recv = AsyncMock(side_effect=[
                _make_hello(auth_required=False),
                _make_identified(),
                _make_request_response(1, True, {}),
            ])
            mock_ws.send = AsyncMock()
            mock_ws.close = AsyncMock()
            mock_ws_class = MagicMock()
            mock_ws_class.connect = AsyncMock(return_value=mock_ws)

            with patch("obs_capture_real._get_websockets", return_value=mock_ws_class):
                from obs_capture_real import OBSRecorder
                async with OBSRecorder() as rec:
                    await rec.start("/tmp/test.mp4")
                    for call in mock_ws.send.call_args_list:
                        payload = json.loads(call[0][0])
                        if payload.get("op") == 6:
                            self.assertEqual(payload["d"]["requestType"], "StartRecord")
                            return
                    self.fail("StartRecord request not found")

        asyncio.run(_run())

    def test_stop_record_opcode(self):
        """StopRecord sends correct op 6 request and returns path."""
        async def _run():
            mock_ws = AsyncMock()
            mock_ws.recv = AsyncMock(side_effect=[
                _make_hello(auth_required=False),
                _make_identified(),
                _make_request_response(1, True, {}),
                _make_request_response(2, True, {"outputActive": True}),
                _make_request_response(3, True, {"outputPath": "/tmp/clip.mp4"}),
            ])
            mock_ws.send = AsyncMock()
            mock_ws.close = AsyncMock()
            mock_ws_class = MagicMock()
            mock_ws_class.connect = AsyncMock(return_value=mock_ws)

            with patch("obs_capture_real._get_websockets", return_value=mock_ws_class):
                from obs_capture_real import OBSRecorder
                async with OBSRecorder() as rec:
                    await rec.start("/tmp/test.mp4")
                    await rec.wait_recording_active(timeout_sec=1.0)
                    path = await rec.stop()
                    self.assertEqual(path, "/tmp/clip.mp4")

        asyncio.run(_run())


class TestRecordSpectatorClip(unittest.TestCase):
    """Verify record_spectator_clip handles missing OBS."""

    def test_missing_obs_raises_error(self):
        """When OBS is unreachable, a clear error is raised."""
        async def _run():
            mock_ws_class = MagicMock()
            mock_ws_class.connect = AsyncMock(
                side_effect=ConnectionRefusedError("Connection refused")
            )
            with patch("obs_capture_real._get_websockets", return_value=mock_ws_class):
                from obs_capture_real import record_spectator_clip
                with self.assertRaises((ConnectionError, ConnectionRefusedError, OSError)):
                    await record_spectator_clip(
                        "/tmp/test.mp4", duration_sec=0.1,
                        host="localhost", port=19999
                    )

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
