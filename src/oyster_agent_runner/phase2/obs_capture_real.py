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
import contextlib
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
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
            self._listener_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send_request(self, request_type: str, params: dict | None = None) -> dict:
        """Send a request and await the matching response."""
        msg_id = self._next_id()
        payload: dict = {
            "op": OP_REQUEST,
            "d": {
                "requestType": request_type,
                "requestId": str(msg_id),
            },
        }
        if params:
            payload["d"]["requestData"] = params

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut

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
        challenge = hello_data.get("authentication", {}).get("challenge", "")
        salt = hello_data.get("authentication", {}).get("salt", "")

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
        if identified.get("op") == OP_IDENTIFIED:
            logger.info("OBS authentication successful.")
        else:
            raise ConnectionError(f"Authentication failed: {identified}")

        # Start background listener for responses
        self._listener_task = asyncio.create_task(self._response_listener())

    async def _response_listener(self) -> None:
        """Continuously read messages and resolve pending futures."""
        try:
            while True:
                raw = await self._ws.recv()
                msg = json.loads(raw)
                op = msg.get("op")
                data = msg.get("d", {})

                if op == OP_REQUEST_RESPONSE:
                    req_id = int(data.get("requestId", "0"))
                    fut = self._pending.pop(req_id, None)
                    if fut and not fut.done():
                        if data.get("requestStatus", {}).get("result", False):
                            fut.set_result(data.get("responseData", {}))
                        else:
                            fut.set_exception(
                                RuntimeError(f"OBS request failed: {data.get('requestStatus', {})}")
                            )
                elif op == 9:  # Event
                    logger.debug("OBS event: %s", data.get("eventType"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Response listener exited: %s", exc)

    async def start(self, output_path: str, profile: str = "spectator-1080p30") -> None:
        """Start OBS recording.

        Args:
            output_path: Desired output file path (OBS may override).
            profile: OBS recording profile name.
        """
        await self._send_request("StartRecord")
        logger.info("Recording started → %s", output_path)

    async def wait_recording_active(self, timeout_sec: float = 5.0) -> None:
        """Poll GetRecordStatus until outputActive is True.

        Args:
            timeout_sec: Maximum seconds to wait.

        Raises:
            TimeoutError: If recording does not become active in time.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_sec
        while loop.time() < deadline:
            status = await self.get_status()
            if status.get("outputActive"):
                return
            await asyncio.sleep(0.2)
        raise TimeoutError("Recording did not become active within timeout.")

    async def stop(self) -> str:
        """Stop OBS recording and return the final output filepath.

        Returns:
            The absolute path of the recorded file.
        """
        result = await self._send_request("StopRecord")
        output_path = result.get("outputPath", "")
        logger.info("Recording stopped → %s", output_path)
        return output_path

    async def get_status(self) -> dict:
        """Query current recording status.

        Returns:
            Dict with keys: recording (bool), paused (bool),
            outputActive (bool), outputBytes (int), outputDuration (float).
        """
        result = await self._send_request("GetRecordStatus")
        return {
            "recording": result.get("outputActive", False),
            "paused": result.get("outputPaused", False),
            "outputActive": result.get("outputActive", False),
            "outputBytes": result.get("outputBytes", 0),
            "outputDuration": result.get("outputDuration", 0.0),
        }


async def record_spectator_clip(
    output_path: str,
    duration_sec: float,
    host: str = "localhost",
    port: int = 4455,
    password: str = "",
) -> str:
    """Convenience helper: record a spectator clip for a fixed duration.

    Opens a recorder, starts recording, waits for it to become active,
    sleeps for *duration_sec*, then stops and returns the output path.

    Args:
        output_path: Desired output file path.
        duration_sec: How long to record in seconds.
        host: OBS websocket host.
        port: OBS websocket port.
        password: OBS websocket password.

    Returns:
        The final recorded file path.

    Raises:
        ConnectionError: If OBS is unreachable or auth fails.
        TimeoutError: If recording does not start in time.
    """
    async with OBSRecorder(ws_host=host, ws_port=port, password=password) as rec:
        await rec.start(output_path)
        await rec.wait_recording_active()
        await asyncio.sleep(duration_sec)
        return await rec.stop()
