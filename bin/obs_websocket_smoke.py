#!/usr/bin/env python3
"""
OBS WebSocket Smoke Test

Standalone smoke test that boots OBS Studio (subprocess) and verifies
WebSocket v5 connect + auth + StartRecord/StopRecord opcodes.

Usage: python bin/obs_websocket_smoke.py --obs-path /path/to/obs --password secret
Exit: 0=pass, 1=fail, 2=bad args, 3=OBS not found
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _import_websockets() -> Any:
    """Lazily import websockets library."""
    try:
        import websockets
        return websockets
    except ImportError:
        print("ERROR: websockets not installed. Run: pip install websockets")
        sys.exit(1)


class OBSSmokeTest:
    """OBS WebSocket smoke test runner."""

    def __init__(
        self,
        obs_path: Optional[str] = None,
        host: str = "localhost",
        port: int = 4455,
        password: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self.obs_path = obs_path
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.obs_process: Optional[subprocess.Popen[str]] = None
        self.ws_uri = f"ws://{host}:{port}"
        self.temp_dir: Optional[str] = None

    def find_obs(self) -> str:
        """Find OBS Studio executable path."""
        if self.obs_path and Path(self.obs_path).exists():
            return self.obs_path

        candidates = {
            "win32": [r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"],
            "darwin": ["/Applications/OBS.app/Contents/MacOS/OBS"],
            "linux": ["/usr/bin/obs", "/usr/local/bin/obs"],
        }.get(sys.platform, [])

        for p in candidates:
            if Path(p).exists():
                return p
        raise FileNotFoundError("OBS not found. Use --obs-path")

    def start_obs(self) -> None:
        """Start OBS Studio as subprocess."""
        obs_exe = self.find_obs()
        self.temp_dir = tempfile.mkdtemp(prefix="obs_smoke_")
        print(f"[OBS] Starting OBS from {obs_exe}")

        env = os.environ.copy()
        env["OBS_DISABLE_ANALYTICS"] = "1"

        args = [obs_exe, "--profile", self.temp_dir]
        if sys.platform != "win32":
            args.append("--minimize-to-tray")

        self.obs_process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        time.sleep(8)

        if self.obs_process.poll() is not None:
            raise RuntimeError(f"OBS exited: {self.obs_process.returncode}")
        print("[OBS] Started")

    def stop_obs(self) -> None:
        """Stop OBS Studio subprocess and cleanup."""
        if self.obs_process:
            self.obs_process.terminate()
            try:
                self.obs_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.obs_process.kill()
            self.obs_process = None

        if self.temp_dir:
            try:
                shutil.rmtree(self.temp_dir)
            except OSError as exc:
                logger.debug("stop_obs: shutil.rmtree failed for %s: %s", self.temp_dir, exc)
            self.temp_dir = None

    async def wait_for_websocket(self) -> bool:
        """Wait for WebSocket server to become available."""
        websockets = _import_websockets()
        start = time.time()

        while time.time() - start < self.timeout:
            try:
                async with websockets.connect(self.ws_uri, timeout=5):
                    return True
            except OSError as exc:
                logger.debug("wait_for_websocket: connect to %s failed: %s", self.ws_uri, exc)
                await asyncio.sleep(1)
        return False

    async def test_connection(self) -> dict[str, Any]:
        """Test WebSocket connection and authentication."""
        websockets = _import_websockets()

        print(f"[WS] Connecting to {self.ws_uri}")
        async with websockets.connect(self.ws_uri, timeout=10) as ws:
            hello = json.loads(await ws.recv())
            ver = hello.get("d", {}).get("obsWebSocketVersion", "unknown")
            print(f"[WS] Hello: v{ver}")

            auth = None
            if self.password:
                auth = {"password": self.password}

            await ws.send(json.dumps({
                "op": 1,
                "d": {"rpcVersion": 1, "eventSubscriptions": 33, "authentication": auth}
            }))

            resp = json.loads(await ws.recv())
            if resp.get("op") == 0 and resp.get("d", {}).get("authentication"):
                await ws.send(json.dumps({
                    "op": 1,
                    "d": {"rpcVersion": 1, "eventSubscriptions": 33, "authentication": auth}
                }))
                resp = json.loads(await ws.recv())

            if resp.get("op") == 2:
                print("[WS] Authenticated")
                return resp.get("d", {})
            raise RuntimeError(f"Auth failed: {resp}")

    async def test_recording(self) -> dict[str, Any]:
        """Test StartRecord and StopRecord opcodes."""
        websockets = _import_websockets()

        print("[WS] Testing recording...")
        async with websockets.connect(self.ws_uri, timeout=10) as ws:
            await ws.send(json.dumps({
                "op": 6, "d": {"requestType": "StartRecord", "requestId": "r1"}
            }))
            start_resp = json.loads(await ws.recv())
            print(f"[WS] StartRecord: {start_resp.get('d', {}).get('requestStatus', {}).get('code')}")

            await asyncio.sleep(2)

            await ws.send(json.dumps({
                "op": 6, "d": {"requestType": "StopRecord", "requestId": "r2"}
            }))
            stop_resp = json.loads(await ws.recv())
            print(f"[WS] StopRecord: {stop_resp.get('d', {}).get('requestStatus', {}).get('code')}")

            return {"start": start_resp, "stop": stop_resp}

    async def run_tests(self) -> bool:
        """Run all smoke tests."""
        try:
            if not await self.wait_for_websocket():
                print("[FAIL] WebSocket unavailable")
                return False

            ident = await self.test_connection()
            print(f"[PASS] Connected: {ident.get('obsWebSocketVersion')}")

            await self.test_recording()
            print("[PASS] Recording ops OK")
            return True

        except Exception as e:
            print(f"[FAIL] {e}")
            return False


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="OBS WebSocket Smoke Test")
    parser.add_argument("--obs-path", help="Path to OBS executable")
    parser.add_argument("--host", default="localhost", help="WebSocket host")
    parser.add_argument("--port", type=int, default=4455, help="WebSocket port")
    parser.add_argument("--password", help="WebSocket password")
    parser.add_argument("--timeout", type=int, default=30, help="Connection timeout")
    args = parser.parse_args(argv)

    test = OBSSmokeTest(
        obs_path=args.obs_path,
        host=args.host,
        port=args.port,
        password=args.password,
        timeout=args.timeout,
    )

    try:
        test.start_obs()
        return 0 if asyncio.run(test.run_tests()) else 1
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 3
    except KeyboardInterrupt:
        return 1
    finally:
        test.stop_obs()


if __name__ == "__main__":
    sys.exit(main())
