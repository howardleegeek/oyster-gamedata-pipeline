"""
obs_capture_real.py — Actual OBS spectator capture orchestrator.

Implements an async client for obs-websocket v5 protocol to control
OBS Studio recording. Supports authenticated and anonymous connections,
SHA256 challenge-response auth, and recording lifecycle management.

Usage:
    async with OBSRecorder(host="localhost", port=4455, password="secret") as rec:
        await rec.start("/tmp/clip.mp4")
        await rec.wait_recording_active()
        path = await rec.stop()

    # Or use the convenience helper:
    path = await record_spectator_clip("/tmp/clip.mp4", duration_sec=10.0)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# obs-websocket v5 opcodes
OP_HELLO = 0
OP_IDENTIFY = 1
OP_IDENTIFIED = 2
OP_REIDENTIFY = 3
OP_REQUEST = 6
OP_REQUEST_RESPONSE = 7


def _get_websockets():
    """Lazy-import websockets to allow module load without the dependency."""
    try:
        import websockets

        return websockets
    except ImportError:
        raise ImportError("websockets is required. Install with: pip install websockets")


class OBSRecorder:
    """Async client for obs-websocket v5 recording control.

    Manages the full lifecycle: connect → authenticate → start recording →
    poll status → stop recording → disconnect.
    """

    def __init__(
        self,
        ws_host: str = "localhost",
        ws_port: int = 4455,
        password: str = "",
    ) -> None:
        """Initialise recorder parameters.

        Args:
            ws_host: OBS websocket host.
            ws_port: OBS websocket port (default 4455).
            password: OBS websocket password (empty for anonymous).
        """
        self._ws_host = ws_host
        self._ws_port = ws_port
        self._password = password
        self._ws: Any = None
        self._msg_id: int = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._listener_task: asyncio.Task | None = None

    @property
    def _uri(self) -> str:
        return f"ws://{self._ws_host}:{self._ws_port}"

    async def __aenter__(self) -> OBSRecorder:
        """Connect to OBS and complete authentication handshake."""
        ws = _get_websockets()
        self._ws = await ws.connect(self._uri)
        await self._authenticate()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Gracefully close the websocket connection."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send_request(self, request_type: str, params: dict | None = None) -> dict:
        """Send a request to OBS and await the response.

        Args:
            request_type: OBS request type (e.g., "StartRecord").
            params: Optional request parameters.

        Returns:
            Response data dict.

        Raises:
            RuntimeError: If OBS returns an error.
        """
        request_id = self._next_id()
        payload = {
            "op": OP_REQUEST,
            "d": {
                "requestType": request_type,
                "requestId": str(request_id),
                **(params or {}),
            },
        }
        fut: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = fut
        await self._ws.send(json.dumps(payload))
        response = await fut
        return response

    async def _authenticate(self) -> None:
        """Perform obs-websocket v5 authentication handshake.

        Handles both authenticated (password required) and anonymous
        (no password) OBS configurations.
        """
        # Step 1: receive Hello (op 0)
        hello_raw = await self._ws.recv()
        hello = json.loads(hello_raw)
        assert hello.get("op") == OP_HELLO, f"Expected Hello, got {hello}"

        hello_data = hello.get("d", {})
        auth_required = hello_data.get("authRequired", False)
        # Handle case where "authentication" is None (anonymous mode)
        auth_info = hello_data.get("authentication") or {}
        challenge = auth_info.get("challenge", "")
        salt = auth_info.get("salt", "")

        # Step 2: send Identify (op 1)
        identify_data: dict = {}
        if auth_required and self._password:
            # SHA256 base64 challenge: base64(SHA256(password + salt + challenge))
            secret = base64.b64encode(
                hashlib.sha256((self._password + salt + challenge).encode("utf-8")).digest()
            ).decode("utf-8")
            identify_data["authentication"] = secret
        elif auth_required and not self._password:
            raise ConnectionError("OBS requires authentication but no password was provided.")

        identify_payload = {
            "op": OP_IDENTIFY,
            "d": {
                "rpcVersion": 1,
                **identify_data,
            },
        }
        await self._ws.send(json.dumps(identify_payload))

        # Step 3: receive Identified (op 2)
        identified_raw = await self._ws.recv()
        identified = json.loads(identified_raw)
        assert identified.get("op") == OP_IDENTIFIED, f"Expected Identified, got {identified}"

    async def start(self, output_path: str | None = None) -> None:
        """Start recording.

        Args:
            output_path: Optional custom output path. If not provided,
                uses OBS's configured default.

        Raises:
            RuntimeError: If recording fails to start.
        """
        if output_path:
            await self._send_request("SetRecordDirectory", {"recordDirectory": output_path})
        await self._send_request("StartRecord")

    async def stop(self) -> str:
        """Stop recording and return the output path.

        Returns:
            Path to the recorded file.

        Raises:
            RuntimeError: If OBS returns an error.
        """
        resp = await self._send_request("StopRecord")
        return resp.get("outputPath", "")

    async def get_status(self) -> dict:
        """Get current recording status.

        Returns:
            Dict with keys: active (bool), paused (bool), timecode (str),
            output_path (str).
        """
        resp = await self._send_request("GetRecordStatus")
        return resp

    async def wait_recording_active(self, timeout: float = 10.0) -> None:
        """Wait until recording is active.

        Args:
            timeout: Max seconds to wait.

        Raises:
            TimeoutError: If recording doesn't start within timeout.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            status = await self.get_status()
            if status.get("outputActive", False):
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("Recording did not start within timeout")

    async def _listen_for_responses(self) -> None:
        """Background task to listen for request responses."""
        try:
            async for msg in self._ws:
                data = json.loads(msg)
                if data.get("op") == OP_REQUEST_RESPONSE:
                    resp_data = data.get("d", {})
                    req_id = int(resp_data.get("requestId", "0"))
                    if req_id in self._pending:
                        fut = self._pending.pop(req_id)
                        if resp_data.get("requestStatus", {}).get("result", False):
                            fut.set_result(resp_data.get("responseData", {}))
                        else:
                            fut.set_exception(
                                RuntimeError(f"OBS error: {resp_data.get('requestStatus', {})}")
                            )
        except asyncio.CancelledError:
            pass

    async def __aenter__(self) -> "OBSRecorder":
        """Enter async context."""
        ws = _get_websockets()
        self._ws = await ws.connect(self._uri)
        await self._authenticate()
        self._listener_task = asyncio.create_task(self._listen_for_responses())
        return self


async def record_spectator_clip(
    output_path: str,
    duration_sec: float,
    ws_host: str = "localhost",
    ws_port: int = 4455,
    password: str = "",
) -> str:
    """Record a spectator clip of the given duration.

    Args:
        output_path: Path to save the recording.
        duration_sec: Duration in seconds.
        ws_host: OBS websocket host.
        ws_port: OBS websocket port.
        password: OBS websocket password.

    Returns:
        Path to the recorded file.
    """
    async with OBSRecorder(ws_host=ws_host, ws_port=ws_port, password=password) as rec:
        await rec.start(output_path)
        await rec.wait_recording_active()
        await asyncio.sleep(duration_sec)
        return await rec.stop()