"""
OBS WebSocket v5 protocol implementation for capturing recordings.
Implements the obs-websocket v5 protocol with authentication support.
"""

import asyncio
import base64
import hashlib
import json
import logging
from typing import Any

# Lazy import
_websockets = None

def _import_websockets():
    global _websockets
    if _websockets is None:
        try:
            import websockets
            _websockets = websockets
        except ImportError:
            raise ImportError("websockets library not installed. Install with: pip install websockets")
    return _websockets


class OBSSpectatorCapture:
    """OBS WebSocket v5 client for starting and stopping recordings."""

    OP_HELLO, OP_IDENTIFY, OP_REQUEST, OP_REQUEST_RESPONSE = 0, 1, 6, 7

    def __init__(self, host: str = "localhost", port: int = 4455, password: str = ""):
        self.host, self.port, self.password = host, port, password
        self.websocket, self.connected = None, False
        self.request_id, self.pending = 0, {}
        self.logger = logging.getLogger(__name__)

    def _auth_hash(self, challenge: str, salt: str) -> str:
        """Generate auth hash: base64(SHA256(base64(SHA256(password + salt)) + challenge))"""
        if not self.password:
            return ""
        secret = hashlib.sha256((self.password + salt).encode()).digest()
        secret_b64 = base64.b64encode(secret).decode()
        auth = hashlib.sha256((secret_b64 + challenge).encode()).digest()
        return base64.b64encode(auth).decode()

    async def connect(self) -> bool:
        """Connect to OBS WebSocket server."""
        try:
            ws = _import_websockets()
            self.websocket = await ws.connect(f"ws://{self.host}:{self.port}")
            self.connected = True
            asyncio.create_task(self._message_handler())
            return True
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self.connected = False
            return False

    async def authenticate(self, challenge: str, salt: str) -> dict[str, Any]:
        """Authenticate with OBS WebSocket server."""
        if not self.connected:
            raise ConnectionError("Not connected")

        auth = self._auth_hash(challenge, salt)
        msg = {"op": self.OP_IDENTIFY, "d": {"rpcVersion": 1}}
        msg["d"]["authentication" if auth else "eventSubscriptions"] = auth or 0

        await self.websocket.send(json.dumps(msg))
        resp = json.loads(await self.websocket.recv())
        if resp.get("op") == self.OP_IDENTIFY:
            return resp.get("d", {})
        raise ValueError(f"Unexpected auth response: {resp}")

    async def _send_request(self, req_type: str, data: dict | None = None) -> dict[str, Any]:
        """Send request and wait for response."""
        if not self.connected:
            raise ConnectionError("Not connected")

        self.request_id += 1
        req_id = f"req_{self.request_id}"
        msg = {"op": self.OP_REQUEST, "d": {"requestType": req_type, "requestId": req_id}}
        if data:
            msg["d"]["requestData"] = data

        future = asyncio.get_event_loop().create_future()
        self.pending[req_id] = future
        await self.websocket.send(json.dumps(msg))

        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except TimeoutError:
            raise TimeoutError(f"Timeout waiting for {req_type}")
        finally:
            self.pending.pop(req_id, None)

    async def _message_handler(self):
        """Handle incoming WebSocket messages."""
        try:
            async for msg in self.websocket:
                data = json.loads(msg)
                op = data.get("op")

                if op == self.OP_HELLO:
                    d = data.get("d", {})
                    auth = d.get("authentication", {})
                    if auth.get("challenge") and auth.get("salt"):
                        await self.authenticate(auth["challenge"], auth["salt"])

                elif op == self.OP_REQUEST_RESPONSE:
                    resp = data.get("d", {})
                    req_id = resp.get("requestId")
                    if req_id in self.pending and not self.pending[req_id].done():
                        self.pending[req_id].set_result(resp)

        except Exception as e:
            self.logger.error(f"Message handler error: {e}")
            self.connected = False

    async def start_recording(self, output_path: str = "") -> bool:
        """Start recording in OBS."""
        try:
            data = {"outputPath": output_path} if output_path else None
            resp = await self._send_request("StartRecord", data)
            return resp.get("requestStatus", {}).get("code", 0) == 100
        except Exception as e:
            self.logger.error(f"Start recording failed: {e}")
            return False

    async def stop_recording(self) -> str:
        """Stop recording and return output file path."""
        try:
            resp = await self._send_request("StopRecord")
            return resp.get("responseData", {}).get("outputPath", "")
        except Exception as e:
            self.logger.error(f"Stop recording failed: {e}")
            return ""

    async def disconnect(self) -> None:
        """Gracefully disconnect from OBS WebSocket."""
        if self.websocket:
            for fut in self.pending.values():
                if not fut.done():
                    fut.cancel()
            self.pending.clear()
            await self.websocket.close()
            self.websocket, self.connected = None, False

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()
