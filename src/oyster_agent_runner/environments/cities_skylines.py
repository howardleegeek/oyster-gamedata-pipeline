#!/usr/bin/env python3
"""
Cities Skylines extractor: Mod API + Harmony patch shim.

Provides an interface to extract game state from Cities Skylines via a Harmony
patch shim and named pipe communication. Captures camera position (3D), zoom,
simulation tick, and other game state data.

Author: G179 Development Team
"""

import argparse
import json
import logging
import os
import platform
import struct
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_PIPE_NAME = "cities_skylines_state"
DEFAULT_TIMEOUT = 5.0
MAGIC_HEADER = b"CSST"


class GameState(Enum):
    """Enumeration of possible game states."""

    LOADING = "loading"
    MAIN_MENU = "main_menu"
    PLAYING = "playing"
    PAUSED = "paused"
    EDITOR = "editor"
    UNKNOWN = "unknown"


@dataclass
class CameraState:
    """Represents the 3D camera state in Cities Skylines."""

    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0
    rotation_x: float = 0.0
    rotation_y: float = 0.0
    rotation_z: float = 0.0
    zoom_level: float = 1.0
    field_of_view: float = 60.0
    target_x: float = 0.0
    target_y: float = 0.0
    target_z: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert camera state to dictionary representation."""
        return {
            "position": [self.position_x, self.position_y, self.position_z],
            "rotation": [self.rotation_x, self.rotation_y, self.rotation_z],
            "zoom": self.zoom_level,
            "field_of_view": self.field_of_view,
            "target": [self.target_x, self.target_y, self.target_z],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CameraState":
        """Create CameraState from dictionary."""
        pos = data.get("position", [0.0, 0.0, 0.0])
        rot = data.get("rotation", [0.0, 0.0, 0.0])
        tgt = data.get("target", [0.0, 0.0, 0.0])
        return cls(
            position_x=float(pos[0]) if len(pos) > 0 else 0.0,
            position_y=float(pos[1]) if len(pos) > 1 else 0.0,
            position_z=float(pos[2]) if len(pos) > 2 else 0.0,
            rotation_x=float(rot[0]) if len(rot) > 0 else 0.0,
            rotation_y=float(rot[1]) if len(rot) > 1 else 0.0,
            rotation_z=float(rot[2]) if len(rot) > 2 else 0.0,
            zoom_level=float(data.get("zoom", 1.0)),
            field_of_view=float(data.get("field_of_view", 60.0)),
            target_x=float(tgt[0]) if len(tgt) > 0 else 0.0,
            target_y=float(tgt[1]) if len(tgt) > 1 else 0.0,
            target_z=float(tgt[2]) if len(tgt) > 2 else 0.0,
        )


@dataclass
class SimulationState:
    """Represents the simulation state including tick and time."""

    tick: int = 0
    time_of_day: float = 12.0
    day: int = 1
    month: int = 1
    year: int = 2020
    speed: float = 1.0
    is_paused: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert simulation state to dictionary representation."""
        return {
            "tick": self.tick,
            "time_of_day": self.time_of_day,
            "date": {"day": self.day, "month": self.month, "year": self.year},
            "speed": self.speed,
            "is_paused": self.is_paused,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SimulationState":
        """Create SimulationState from dictionary."""
        date = data.get("date", {})
        return cls(
            tick=int(data.get("tick", 0)),
            time_of_day=float(data.get("time_of_day", 12.0)),
            day=int(date.get("day", 1)),
            month=int(date.get("month", 1)),
            year=int(date.get("year", 2020)),
            speed=float(data.get("speed", 1.0)),
            is_paused=bool(data.get("is_paused", False)),
        )


@dataclass
class GameStateSnapshot:
    """Complete game state snapshot combining all state data."""

    timestamp: float = field(default_factory=time.time)
    game_state: GameState = GameState.UNKNOWN
    camera: CameraState = field(default_factory=CameraState)
    simulation: SimulationState = field(default_factory=SimulationState)
    city_name: str = ""
    money: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert complete game state to dictionary."""
        return {
            "timestamp": self.timestamp,
            "game_state": self.game_state.value,
            "camera": self.camera.to_dict(),
            "simulation": self.simulation.to_dict(),
            "city_name": self.city_name,
            "money": self.money,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameStateSnapshot":
        """Create GameStateSnapshot from dictionary."""
        return cls(
            timestamp=float(data.get("timestamp", time.time())),
            game_state=GameState(data.get("game_state", "unknown")),
            camera=CameraState.from_dict(data.get("camera", {})),
            simulation=SimulationState.from_dict(data.get("simulation", {})),
            city_name=str(data.get("city_name", "")),
            money=int(data.get("money", 0)),
        )


class NamedPipeTransport:
    """Handles named pipe communication for game state extraction."""

    def __init__(self, pipe_name: str = DEFAULT_PIPE_NAME, timeout: float = DEFAULT_TIMEOUT):
        """Initialize the named pipe transport."""
        self.pipe_name = pipe_name
        self.timeout = timeout
        self._pipe_path = self._get_pipe_path()
        self._pipe_fd: Optional[int] = None

    def _get_pipe_path(self) -> str:
        """Get platform-specific pipe path."""
        if platform.system() == "Windows":
            return f"\\\\.\\pipe\\{self.pipe_name}"
        return os.path.join(tempfile.gettempdir(), self.pipe_name)

    def connect(self) -> bool:
        """Connect to the named pipe. Returns True if successful."""
        try:
            if os.path.exists(self._pipe_path):
                self._pipe_fd = os.open(self._pipe_path, os.O_RDWR | os.O_NONBLOCK)
                logger.info(f"Connected to pipe: {self._pipe_path}")
                return True
            logger.warning(f"Pipe does not exist: {self._pipe_path}")
            return False
        except OSError as e:
            logger.error(f"Failed to connect to pipe: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from the named pipe."""
        if self._pipe_fd is not None:
            try:
                os.close(self._pipe_fd)
            except OSError as e:
                logger.warning(f"Failed to close pipe fd {self._pipe_fd}: {e}")
            self._pipe_fd = None
            logger.info("Disconnected from pipe")

    def read_state(self) -> Optional[GameStateSnapshot]:
        """Read game state from the pipe."""
        if self._pipe_fd is None:
            logger.error("Not connected to pipe")
            return None
        try:
            data = self._read_frame()
            if data is None:
                return None
            return GameStateSnapshot.from_dict(json.loads(data.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError, struct.error) as e:
            logger.error(f"Failed to parse state data: {e}")
            return None

    def _read_frame(self) -> Optional[bytes]:
        """Read a framed message from the pipe."""
        header = os.read(self._pipe_fd, 8)
        if len(header) < 8:
            return None
        magic, length = struct.unpack("<4sI", header)
        if magic != MAGIC_HEADER:
            logger.error(f"Invalid magic header: {magic}")
            return None
        data = b""
        while length > 0:
            chunk = os.read(self._pipe_fd, min(length, 4096))
            if not chunk:
                break
            data += chunk
            length -= len(chunk)
        return data if len(data) == length else None

    def write_command(self, command: str, params: Optional[Dict[str, Any]] = None) -> bool:
        """Write a command to the pipe. Returns True if successful."""
        if self._pipe_fd is None:
            logger.error("Not connected to pipe")
            return False
        try:
            payload = json.dumps({"command": command, "params": params or {}})
            data = payload.encode("utf-8")
            frame = MAGIC_HEADER + struct.pack("<I", len(data)) + data
            os.write(self._pipe_fd, frame)
            return True
        except OSError as e:
            logger.error(f"Failed to write command: {e}")
            return False

    def __enter__(self) -> "NamedPipeTransport":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()


class CitiesSkylinesExtractor:
    """Main extractor class for Cities Skylines game state."""

    def __init__(self, pipe_name: str = DEFAULT_PIPE_NAME, timeout: float = DEFAULT_TIMEOUT):
        """Initialize the extractor."""
        self.transport = NamedPipeTransport(pipe_name, timeout)
        self._connected = False

    def connect(self) -> bool:
        """Connect to the game via named pipe."""
        if self.transport.connect():
            self._connected = True
            return True
        return False

    def disconnect(self) -> None:
        """Disconnect from the game."""
        self.transport.disconnect()
        self._connected = False

    def get_state(self) -> Optional[GameStateSnapshot]:
        """Get the current game state."""
        if not self._connected:
            logger.error("Not connected to game")
            return None
        self.transport.write_command("get_state")
        return self.transport.read_state()

    def get_camera(self) -> Optional[CameraState]:
        """Get current camera state."""
        state = self.get_state()
        return state.camera if state else None

    def get_simulation_tick(self) -> int:
        """Get current simulation tick. Returns -1 if unavailable."""
        state = self.get_state()
        return state.simulation.tick if state else -1

    def move_camera(self, x: float, y: float, z: float) -> bool:
        """Move the camera to specified position."""
        return self.transport.write_command("set_camera", {"x": x, "y": y, "z": z})

    def set_speed(self, speed: float) -> bool:
        """Set simulation speed (0 = paused)."""
        return self.transport.write_command("set_speed", {"speed": speed})

    def __enter__(self) -> "CitiesSkylinesExtractor":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for the Cities Skylines extractor CLI."""
    parser = argparse.ArgumentParser(description="Cities Skylines game state extractor")
    parser.add_argument("--pipe-name", default=DEFAULT_PIPE_NAME, help="Named pipe name")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Timeout in seconds")
    parser.add_argument(
        "--command",
        choices=["get-state", "get-camera", "get-tick", "move-camera", "set-speed"],
        default="get-state",
        help="Command to execute",
    )
    parser.add_argument("--x", type=float, default=0.0, help="X coordinate for move-camera")
    parser.add_argument("--y", type=float, default=0.0, help="Y coordinate for move-camera")
    parser.add_argument("--z", type=float, default=0.0, help="Z coordinate for move-camera")
    parser.add_argument("--speed", type=float, default=1.0, help="Speed for set-speed")
    parser.add_argument("--output", "-o", help="Output file for state")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        with CitiesSkylinesExtractor(args.pipe_name, args.timeout) as extractor:
            if args.command == "get-state":
                state = extractor.get_state()
                if state:
                    output = state.to_json()
                    if args.output:
                        with open(args.output, "w") as f:
                            f.write(output)
                    else:
                        print(output)
                    return 0
                logger.error("Failed to get game state")
                return 1
            elif args.command == "get-camera":
                camera = extractor.get_camera()
                if camera:
                    print(json.dumps(camera.to_dict(), indent=2))
                    return 0
                logger.error("Failed to get camera state")
                return 1
            elif args.command == "get-tick":
                tick = extractor.get_simulation_tick()
                if tick >= 0:
                    print(tick)
                    return 0
                logger.error("Failed to get simulation tick")
                return 1
            elif args.command == "move-camera":
                if extractor.move_camera(args.x, args.y, args.z):
                    logger.info(f"Camera moved to ({args.x}, {args.y}, {args.z})")
                    return 0
                logger.error("Failed to move camera")
                return 1
            elif args.command == "set-speed":
                if extractor.set_speed(args.speed):
                    logger.info(f"Simulation speed set to {args.speed}")
                    return 0
                logger.error("Failed to set simulation speed")
                return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
