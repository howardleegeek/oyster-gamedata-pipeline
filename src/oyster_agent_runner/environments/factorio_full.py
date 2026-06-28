#!/usr/bin/env python3
"""
Factorio Full Environment Extractor v2.

Full RCON + observer-mod loop for extracting:
- Player pose (position, orientation, facing)
- Tile state
- Biter wave events

Usage:
    python factorio_full.py --host 127.0.0.1 --port 27015 --password secret
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

# RCON protocol constants
RCON_AUTH: int = 3
RCON_EXEC_COMMAND: int = 2
DEFAULT_RCON_PORT: int = 27015
DEFAULT_TIMEOUT: float = 10.0
POLL_INTERVAL: float = 0.1
MAX_OBSERVATIONS: int = 10_000

_FACING_8_TABLE: list[str] = [
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
]


def orientation_to_facing(orientation: float) -> str:
    """Convert Factorio orientation [0,1) to cardinal facing string."""
    idx = int(round(orientation * 8)) % 8
    return _FACING_8_TABLE[idx]


@dataclass
class PlayerState:
    """Player pose and status in the Factorio world."""

    position: tuple[float, float] = (0.0, 0.0)
    orientation: float = 0.0
    facing: str = "north"
    surface: str = "nauvis"
    force: str = "player"
    health: float = 100.0
    selected_entity: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": {"x": self.position[0], "y": self.position[1]},
            "orientation": self.orientation,
            "facing": self.facing,
            "surface": self.surface,
            "force": self.force,
            "health": self.health,
            "selected_entity": self.selected_entity,
        }


@dataclass
class TileState:
    """State of a tile in the Factorio world."""

    position: tuple[int, int] = (0, 0)
    name: str = "dirt"
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": {"x": self.position[0], "y": self.position[1]},
            "name": self.name,
            "hidden": self.hidden,
        }


@dataclass
class BiterWaveEvent:
    """Biter wave attack event data."""

    tick: int = 0
    position: tuple[float, float] = (0.0, 0.0)
    size: int = 0
    evolution: float = 0.0
    target: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "position": {"x": self.position[0], "y": self.position[1]},
            "size": self.size,
            "evolution": self.evolution,
            "target": self.target,
        }


@dataclass
class GameObservation:
    """Complete game observation snapshot."""

    tick: int = 0
    player: PlayerState = field(default_factory=PlayerState)
    tiles: list[TileState] = field(default_factory=list)
    biter_waves: list[BiterWaveEvent] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "player": self.player.to_dict(),
            "tiles": [t.to_dict() for t in self.tiles],
            "biter_waves": [w.to_dict() for w in self.biter_waves],
            "timestamp": self.timestamp,
        }


class RCONConnection:
    """RCON connection handler for Factorio servers."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_RCON_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.authenticated = False
        self._packet_id = 0

    def connect(self) -> None:
        """Establish TCP connection to RCON server."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))
        logger.info(f"Connected to {self.host}:{self.port}")

    def authenticate(self, password: str) -> bool:
        """Authenticate with the RCON server."""
        if not self.sock:
            raise RuntimeError("Not connected to server")
        self._send_packet(RCON_AUTH, password)
        req_id, _, _ = self._recv_packet()
        self.authenticated = req_id != -1
        if self.authenticated:
            logger.info("RCON authentication successful")
        else:
            logger.error("RCON authentication failed")
        return self.authenticated

    def execute(self, command: str) -> str:
        """Execute a command on the RCON server."""
        if not self.sock or not self.authenticated:
            raise RuntimeError("Not authenticated with server")
        self._send_packet(RCON_EXEC_COMMAND, command)
        _, _, body = self._recv_packet()
        return body

    def close(self) -> None:
        """Close the RCON connection."""
        if self.sock:
            self.sock.close()
            self.sock = None
            self.authenticated = False
            logger.info("RCON connection closed")

    def _send_packet(self, packet_type: int, body: str) -> None:
        """Send an RCON packet to the server."""
        if not self.sock:
            raise RuntimeError("Not connected to server")
        self._packet_id = (self._packet_id + 1) & 0xFFFFFFFF
        body_bytes = body.encode("utf-8") + b"\x00\x00"
        header = struct.pack("<iii", self._packet_id, packet_type, len(body_bytes))
        self.sock.sendall(header + body_bytes)

    def _recv_packet(self) -> tuple[int, int, str]:
        """Receive an RCON packet from the server."""
        if not self.sock:
            raise RuntimeError("Not connected to server")
        header = self._recv_exact(12)
        packet_id, packet_type, size = struct.unpack("<iii", header)
        body_bytes = self._recv_exact(size)
        body = body_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")
        return packet_id, packet_type, body

    def _recv_exact(self, size: int) -> bytes:
        """Receive exactly N bytes from the socket."""
        if not self.sock:
            raise RuntimeError("Not connected to server")
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("Connection closed by server")
            data += chunk
        return data

    def __enter__(self) -> "RCONConnection":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class FactorioObserver:
    """Factorio game state observer using RCON."""

    def __init__(
        self,
        rcon: RCONConnection,
        poll_interval: float = POLL_INTERVAL,
        max_observations: int = MAX_OBSERVATIONS,
    ) -> None:
        self.rcon = rcon
        self.poll_interval = poll_interval
        self.max_observations = max_observations

    def get_player_state(self, player_name: Optional[str] = None) -> PlayerState:
        """Query current player state from the server."""
        player_ref = f'game.players["{player_name}"]' if player_name else "game.players[1]"
        cmd = f"/c local p={player_ref};rcon.print(game.table_to_json(p and {{x=p.position.x,y=p.position.y,o=p.character and p.character.orientation or 0,s=p.surface.name,f=p.force.name,h=p.character and p.character.health or 100,sel=p.selected and p.selected.name or nil}} or {{}}))"
        try:
            response = self.rcon.execute(cmd)
            data = json.loads(response.strip()) if response.strip() else {}
        except (json.JSONDecodeError, RuntimeError) as e:
            logger.warning(f"Failed to parse player state: {e}")
            return PlayerState()

        return PlayerState(
            position=(float(data.get("x", 0)), float(data.get("y", 0))),
            orientation=float(data.get("o", 0)),
            facing=orientation_to_facing(float(data.get("o", 0))),
            surface=str(data.get("s", "nauvis")),
            force=str(data.get("f", "player")),
            health=float(data.get("h", 100)),
            selected_entity=data.get("sel"),
        )

    def get_tile_state(self, center: tuple[float, float], radius: int = 10) -> list[TileState]:
        """Query tile states around a center position."""
        cmd = f"/c local t=game.surfaces[1].find_tiles_filtered({{position={{{center[0]},{center[1]}}},radius={radius}}})local r={{}}for i,v in ipairs(t)do r[i]={{x=v.position.x,y=v.position.y,n=v.name}}end rcon.print(game.table_to_json(r))"
        try:
            response = self.rcon.execute(cmd)
            data = json.loads(response.strip()) if response.strip() else []
        except (json.JSONDecodeError, RuntimeError) as e:
            logger.warning(f"Failed to parse tile state: {e}")
            return []

        return [
            TileState(
                position=(int(t.get("x", 0)), int(t.get("y", 0))), name=str(t.get("n", "dirt"))
            )
            for t in (data if isinstance(data, list) else [])
        ]

    def get_biter_wave_events(self) -> list[BiterWaveEvent]:
        """Query active biter wave events from the server."""
        cmd = "/c rcon.print(game.table_to_json(global.biter_waves or {}))"
        try:
            response = self.rcon.execute(cmd)
            data = json.loads(response.strip()) if response.strip() else []
        except (json.JSONDecodeError, RuntimeError) as e:
            logger.warning(f"Failed to parse biter wave events: {e}")
            return []

        waves = []
        for w in data if isinstance(data, list) else []:
            pos = w.get("position", {})
            waves.append(
                BiterWaveEvent(
                    tick=int(w.get("tick", 0)),
                    position=(float(pos.get("x", 0)), float(pos.get("y", 0))),
                    size=int(w.get("size", 0)),
                    evolution=float(w.get("evolution", 0)),
                    target=w.get("target"),
                )
            )
        return waves

    def observe(self, player_name: Optional[str] = None) -> GameObservation:
        """Capture a complete game observation snapshot."""
        player = self.get_player_state(player_name)
        tiles = self.get_tile_state(player.position)
        waves = self.get_biter_wave_events()

        tick = 0
        try:
            tick_response = self.rcon.execute("/c rcon.print(game.tick)")
            tick = int(tick_response.strip()) if tick_response.strip() else 0
        except (ValueError, RuntimeError):
            pass

        return GameObservation(
            tick=tick,
            player=player,
            tiles=tiles,
            biter_waves=waves,
            timestamp=time.time(),
        )

    def observe_loop(
        self,
        callback: Optional[Callable[[GameObservation], None]] = None,
        player_name: Optional[str] = None,
    ) -> Iterator[GameObservation]:
        """Run continuous observation loop."""
        count = 0
        while count < self.max_observations:
            obs = self.observe(player_name)
            if callback:
                callback(obs)
            yield obs
            count += 1
            time.sleep(self.poll_interval)


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for the Factorio observer CLI."""
    parser = argparse.ArgumentParser(
        description="Factorio Full Environment Extractor v2",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Factorio server RCON host")
    parser.add_argument("--port", type=int, default=DEFAULT_RCON_PORT, help="RCON port")
    parser.add_argument(
        "--password", default="", help="RCON password (or set FACTORIO_RCON_PASSWORD)"
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Connection timeout")
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL, help="Poll interval")
    parser.add_argument(
        "--max-observations", type=int, default=MAX_OBSERVATIONS, help="Max observations"
    )
    parser.add_argument("--output", "-o", type=Path, help="Output file (JSONL format)")
    parser.add_argument("--player", default=None, help="Specific player to observe")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    password = args.password or os.environ.get("FACTORIO_RCON_PASSWORD", "")
    if not password:
        logger.error("No RCON password provided")
        return 1

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as output_file:
            try:
                with RCONConnection(args.host, args.port, args.timeout) as rcon:
                    if not rcon.authenticate(password):
                        logger.error("Authentication failed")
                        return 1

                    observer = FactorioObserver(rcon, args.poll_interval, args.max_observations)
                    for obs in observer.observe_loop(player_name=args.player):
                        line = json.dumps(obs.to_dict())
                        output_file.write(line + "\n")
                        logger.info(f"Tick {obs.tick}: Player at {obs.player.position}")

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
            except Exception as e:
                logger.error(f"Error: {e}")
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
