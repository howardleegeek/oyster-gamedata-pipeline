#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cyberpunk_2077.py — CP2077 game-state extractor via Cyber Engine Tweaks (CET).

Connects to the CET Lua websocket endpoint, executes remote Lua snippets to
extract 6-DoF camera pose, player position/velocity, field-of-view, and
day-night cycle parameters.  Designed for the oyster_agent_runner harness.

Usage (CLI):
    python -m oyster_agent_runner.environments.cyberpunk_2077 --host 127.0.0.1 --port 6080
    python -m oyster_agent_runner.environments.cyberpunk_2077 snapshot --out state.json

Author : oyster_agent_runner team
License: MIT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import struct
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Vector3:
    """Simple 3-D vector."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_list(self) -> List[float]:
        """Convert the vector to a list of [x, y, z] floats.

        Returns:
            A list containing the x, y, z components in order.
        """
        return [self.x, self.y, self.z]

    def length(self) -> float:
        """Calculate the Euclidean length (magnitude) of this 3D vector.

        Returns:
            The square root of x^2 + y^2 + z^2.
        """
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)


@dataclass
class Quaternion:
    """Unit quaternion (w, x, y, z)."""

    w: float = 1.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_list(self) -> List[float]:
        return [self.w, self.x, self.y, self.z]

    def to_euler(self) -> Vector3:
        """Convert quaternion to Euler angles (roll, pitch, yaw) in degrees."""
        sinr_cosp = 2.0 * (self.w * self.x + self.y * self.z)
        cosr_cosp = 1.0 - 2.0 * (self.x**2 + self.y**2)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        sinp = 2.0 * (self.w * self.y - self.z * self.x)
        pitch = math.asin(max(-1.0, min(1.0, sinp)))

        siny_cosp = 2.0 * (self.w * self.z + self.x * self.y)
        cosy_cosp = 1.0 - 2.0 * (self.y**2 + self.z**2)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        return Vector3(
            math.degrees(roll),
            math.degrees(pitch),
            math.degrees(yaw),
        )


@dataclass
class CameraState:
    """6-DoF camera snapshot."""

    position: Vector3 = field(default_factory=Vector3)
    rotation: Quaternion = field(default_factory=Quaternion)
    fov_horizontal: float = 0.0
    fov_vertical: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["rotation_euler"] = self.rotation.to_euler().to_list()
        return d


@dataclass
class PlayerState:
    """Player pose and kinematics."""

    position: Vector3 = field(default_factory=Vector3)
    rotation: Quaternion = field(default_factory=Quaternion)
    velocity: Vector3 = field(default_factory=Vector3)
    is_in_vehicle: bool = False
    is_crouching: bool = False
    is_sprinting: bool = False

    @property
    def speed(self) -> float:
        """Computed speed magnitude from velocity vector."""
        return self.velocity.length()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["rotation_euler"] = self.rotation.to_euler().to_list()
        d["speed"] = self.speed
        return d


@dataclass
class DayNightState:
    """In-game day / night cycle parameters."""

    game_hour: float = 0.0
    game_minute: float = 0.0
    day_progress: float = 0.0  # 0.0 = midnight, 0.5 = noon
    weather_id: str = ""
    weather_intensity: float = 0.0

    @property
    def is_night(self) -> bool:
        """True when game hour is between 20:00 and 06:00."""
        return self.game_hour >= 20.0 or self.game_hour < 6.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_night"] = self.is_night
        return d


@dataclass
class GameSnapshot:
    """Full game-state snapshot."""

    timestamp: float = 0.0
    frame_id: int = 0
    camera: CameraState = field(default_factory=CameraState)
    player: PlayerState = field(default_factory=PlayerState)
    day_night: DayNightState = field(default_factory=DayNightState)
    raw_lua: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "frame_id": self.frame_id,
            "camera": self.camera.to_dict(),
            "player": self.player.to_dict(),
            "day_night": self.day_night.to_dict(),
            "raw_lua": self.raw_lua,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# CET WebSocket client (stdlib-only, no websockets dependency)
# ---------------------------------------------------------------------------

# CET default endpoint
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6080

# Lua scripts executed remotely via CET websocket
LUA_CAMERA_SCRIPT = """
local cam = Game.GetCamera()
local pos = cam:GetWorldPosition()
local rot = cam:GetWorldOrientation()
local fov = cam:GetFOV()
return {
    pos = {x = pos.x, y = pos.y, z = pos.z},
    rot = {w = rot.w, x = rot.x, y = rot.y, z = rot.z},
    fov_h = fov.horizontal,
    fov_v = fov.vertical
}
"""

LUA_PLAYER_SCRIPT = """
local player = Game.GetPlayer()
local pos = player:GetWorldPosition()
local rot = player:GetWorldOrientation()
local vel = player:GetWorldVelocity()
return {
    pos = {x = pos.x, y = pos.y, z = pos.z},
    rot = {w = rot.w, x = rot.x, y = rot.y, z = rot.z},
    vel = {x = vel.x, y = vel.y, z = vel.z},
    in_vehicle = player:IsInVehicle(),
    crouching = player:IsCrouching(),
    sprinting = player:IsSprinting()
}
"""

LUA_DAYNIGHT_SCRIPT = """
local time = Game.GetTimeSystem()
local hour = time:GetGameHour()
local minute = time:GetGameMinute()
local progress = time:GetDayProgress()
local weather = Game.GetWeatherSystem()
local w_id = weather:GetCurrentWeather():GetName() or "unknown"
local w_int = weather:GetCurrentWeather():GetIntensity() or 0.0
return {
    hour = hour,
    minute = minute,
    progress = progress,
    weather_id = w_id,
    weather_intensity = w_int
}
"""


class CETWebSocketClient:
    """Minimal CET websocket client using only stdlib (asyncio streams).

    Implements a simplified websocket handshake + frame parser sufficient
    for text-frame communication with the CET Lua console.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._frame_counter = 0

    async def connect(self, timeout: float = 10.0) -> None:
        """Open TCP connection and perform websocket handshake."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=timeout,
            )
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as exc:
            raise ConnectionError(
                f"Cannot connect to CET at {self.host}:{self.port}: {exc}"
            ) from exc

        # Websocket handshake
        import base64

        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._writer.write(handshake.encode())
        await self._writer.drain()

        response = await asyncio.wait_for(self._reader.readuntil(b"\r\n\r\n"), timeout=timeout)
        if b"101" not in response:
            self.close()
            raise ConnectionError(f"Websocket handshake failed: {response.decode()}")
        self._connected = True
        logger.info("Connected to CET websocket at %s:%d", self.host, self.port)

    def close(self) -> None:
        """Close the websocket connection."""
        if self._writer and not self._writer.is_closing():
            self._writer.close()
        self._connected = False

    async def _read_frame(self) -> bytes:
        """Read a single websocket frame payload (text or binary)."""
        if self._reader is None:
            raise RuntimeError("Not connected")

        header = await self._reader.readexactly(2)
        fin = (header[0] & 0x80) != 0
        opcode = header[0] & 0x0F
        masked = (header[1] & 0x80) != 0
        payload_len = header[1] & 0x7F

        if payload_len == 126:
            ext = await self._reader.readexactly(2)
            payload_len = struct.unpack("!H", ext)[0]
        elif payload_len == 127:
            ext = await self._reader.readexactly(8)
            payload_len = struct.unpack("!Q", ext)[0]

        mask_key = b""
        if masked:
            mask_key = await self._reader.readexactly(4)

        payload = await self._reader.readexactly(payload_len)

        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        if opcode == 0x08:  # close
            self._connected = False
            raise ConnectionError("Server sent close frame")

        if not fin:
            raise RuntimeError("Received fragmented frame")

        return payload

    async def send_text(self, text: str) -> None:
        """Send a text websocket frame."""
        if self._writer is None:
            raise RuntimeError("Not connected")

        data = text.encode("utf-8")
        length = len(data)

        frame = bytearray()
        frame.append(0x81)  # FIN + text opcode

        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.append(126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(127)
            frame.extend(struct.pack("!Q", length))

        frame.extend(data)
        self._writer.write(bytes(frame))
        await self._writer.drain()

    async def execute_lua(self, lua_script: str, timeout: float = 5.0) -> Any:
        """Execute a Lua snippet via CET and return parsed JSON result."""
        if not self._connected:
            raise RuntimeError("Not connected to CET")

        self._frame_counter += 1
        cmd = json.dumps({"cmd": "eval", "script": lua_script})
        await self.send_text(cmd)

        raw = await asyncio.wait_for(self._read_frame(), timeout=timeout)
        text = raw.decode("utf-8")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Non-JSON CET response: %s", text[:200])
            return {"raw": text}


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------


def _parse_vector3(data: Optional[Dict[str, float]]) -> Vector3:
    if data is None:
        return Vector3()
    return Vector3(
        x=float(data.get("x", 0)),
        y=float(data.get("y", 0)),
        z=float(data.get("z", 0)),
    )


def _parse_quaternion(data: Optional[Dict[str, float]]) -> Quaternion:
    if data is None:
        return Quaternion()
    return Quaternion(
        w=float(data.get("w", 1)),
        x=float(data.get("x", 0)),
        y=float(data.get("y", 0)),
        z=float(data.get("z", 0)),
    )


async def capture_snapshot(
    client: CETWebSocketClient,
    frame_id: int = 0,
) -> GameSnapshot:
    """Capture a full game-state snapshot from CP2077 via CET."""
    snapshot = GameSnapshot(
        timestamp=time.time(),
        frame_id=frame_id,
    )

    # Camera
    try:
        cam_data = await client.execute_lua(LUA_CAMERA_SCRIPT)
        if isinstance(cam_data, dict):
            snapshot.camera.position = _parse_vector3(cam_data.get("pos"))
            snapshot.camera.rotation = _parse_quaternion(cam_data.get("rot"))
            snapshot.camera.fov_horizontal = float(cam_data.get("fov_h", 0))
            snapshot.camera.fov_vertical = float(cam_data.get("fov_v", 0))
            snapshot.raw_lua["camera"] = cam_data
    except Exception as exc:
        logger.error("Camera capture failed: %s", exc)

    # Player
    try:
        player_data = await client.execute_lua(LUA_PLAYER_SCRIPT)
        if isinstance(player_data, dict):
            snapshot.player.position = _parse_vector3(player_data.get("pos"))
            snapshot.player.rotation = _parse_quaternion(player_data.get("rot"))
            snapshot.player.velocity = _parse_vector3(player_data.get("vel"))
            snapshot.player.is_in_vehicle = bool(player_data.get("in_vehicle", False))
            snapshot.player.is_crouching = bool(player_data.get("crouching", False))
            snapshot.player.is_sprinting = bool(player_data.get("sprinting", False))
            snapshot.raw_lua["player"] = player_data
    except Exception as exc:
        logger.error("Player capture failed: %s", exc)

    # Day / Night
    try:
        dn_data = await client.execute_lua(LUA_DAYNIGHT_SCRIPT)
        if isinstance(dn_data, dict):
            snapshot.day_night.game_hour = float(dn_data.get("hour", 0))
            snapshot.day_night.game_minute = float(dn_data.get("minute", 0))
            snapshot.day_night.day_progress = float(dn_data.get("progress", 0))
            snapshot.day_night.is_night = (
                snapshot.day_night.game_hour >= 20 or snapshot.day_night.game_hour < 6
            )
            snapshot.day_night.weather_id = str(dn_data.get("weather_id", ""))
            snapshot.day_night.weather_intensity = float(dn_data.get("weather_intensity", 0))
            snapshot.raw_lua["day_night"] = dn_data
    except Exception as exc:
        logger.error("Day-night capture failed: %s", exc)

    return snapshot


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CP2077 extractor CLI."""
    parser = argparse.ArgumentParser(
        prog="cyberpunk_2077",
        description="CP2077 game-state extractor via Cyber Engine Tweaks (CET) websocket.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"CET websocket host (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"CET websocket port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connection timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=1,
        help="Number of snapshots to capture (default: 1)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Seconds between snapshots (default: 0)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )

    sub = parser.add_subparsers(dest="command")

    snap = sub.add_parser("snapshot", help="Capture snapshot(s) and output JSON")
    snap.add_argument("--out", "-o", help="Output file path (default: stdout)")
    snap.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )

    sub.add_parser("test", help="Test CET connection only")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point — parse args, connect to CET, capture snapshots."""
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    client = CETWebSocketClient(host=args.host, port=args.port)

    async def _run() -> int:
        try:
            await client.connect(timeout=args.timeout)
        except ConnectionError as exc:
            logger.error("%s", exc)
            return 1

        try:
            if args.command == "test":
                result = await client.execute_lua("return {ok = true}")
                if isinstance(result, dict) and result.get("ok"):
                    logger.info("CET connection OK")
                    return 0
                logger.warning("Unexpected test response: %s", result)
                return 1

            # Default: snapshot
            snapshots: List[GameSnapshot] = []
            for i in range(args.frames):
                snap = await capture_snapshot(client, frame_id=i)
                snapshots.append(snap)
                if args.interval > 0 and i < args.frames - 1:
                    await asyncio.sleep(args.interval)

            # Output
            if len(snapshots) == 1:
                output = snapshots[0].to_json(indent=2 if args.pretty else None)
            else:
                output = json.dumps(
                    [s.to_dict() for s in snapshots],
                    indent=2 if args.pretty else None,
                )

            if args.out:
                out_path = args.out
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(output)
                logger.info("Snapshot written to %s", out_path)
            else:
                print(output)

            return 0

        except Exception as exc:
            logger.error("Capture failed: %s", exc)
            return 1
        finally:
            client.close()

    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
