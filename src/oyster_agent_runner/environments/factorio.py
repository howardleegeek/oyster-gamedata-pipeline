"""Factorio environment adapter — STUB with real connection-string parsing.

Integration target: Factorio RCON over TCP + the official modding API.

Connection-string grammar
-------------------------
We accept a URI-ish form so CLI callers can pass a single string:

    rcon://[password@]host[:port]

Examples:
    rcon://localhost
    rcon://:secret@localhost:25575
    rcon://prod.example.com:25575

Ports default to 25575 (Factorio's default RCON port).

Real integration (TODO)
-----------------------
Requires all three to be production-ready:

  1. **Factorio server binary** — ships with `factorio --server` flag;
     launch with `--rcon-port 25575 --rcon-password <pw>` and a
     `--mod-directory` pointing at our observation mod.

  2. **Observation mod** (Lua, loaded via `mod-list.json`):

         /c rcon.print(game.table_to_json({
             tick = game.tick,
             player = { x = p.position.x, y = p.position.y, ... },
             inventory = inv_to_json(p.get_main_inventory()),
             nearby_entities = nearby_to_json(p.surface, p.position, 32),
         }))

     This is sent via the RCON `say` / `command` frame; the mod writes
     the JSON blob back on the same socket.

  3. **Action shapes** — we standardize on a Lua-dispatch JSON schema:

         {"op": "move", "direction": "north", "ticks": 60}
         {"op": "craft", "recipe": "iron-plate", "count": 10}
         {"op": "place", "entity": "assembling-machine-1", "x": 1.5, "y": 2.5}
         {"op": "mine", "entity_id": 12345}

     The Lua side switches on `op` and dispatches to `player.mining_state`,
     `player.crafting_queue.add`, `player.surface.create_entity`, etc.

Legal: Factorio has an explicit modding API and the developer (Wube)
endorses headless-server research. Single-player and private multiplayer
only — NEVER touch public community servers without consent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from oyster_agent_runner.environments.base import Action, Environment, Observation

_NOT_IMPLEMENTED_MSG = (
    "FactorioEnvironment.step is a scaffold stub. Real integration pending: "
    "Factorio RCON + `oyster-factorio-obs` mod. Use MockEnvironment for tests."
)

# rcon://[password@]host[:port]
_RCON_URI_RE = re.compile(
    r"^rcon://" r"(?::(?P<password>[^@]*)@)?" r"(?P<host>[^:@/]+)" r"(?::(?P<port>\d+))?" r"/?$"
)

DEFAULT_RCON_PORT = 25575


@dataclass(frozen=True)
class RconConnection:
    """Parsed RCON connection params — pure data, no socket state."""

    host: str
    port: int
    password: str

    @classmethod
    def parse(cls, uri: str) -> RconConnection:
        """Parse a `rcon://[pw@]host[:port]` URI. Raises ValueError on malformed input."""
        match = _RCON_URI_RE.match(uri.strip())
        if match is None:
            raise ValueError(f"Invalid RCON URI: {uri!r}. Expected rcon://[password@]host[:port]")
        port_str = match.group("port")
        port = int(port_str) if port_str else DEFAULT_RCON_PORT
        return cls(
            host=match.group("host"),
            port=port,
            password=match.group("password") or "",
        )


class FactorioEnvironment(Environment):
    """Placeholder implementing the `Environment` protocol.

    Constructors accept either discrete params or a single `rcon_uri` string,
    whichever is more ergonomic for the call site. The URI form is preferred
    since it's what the CLI passes through unchanged.
    """

    def __init__(
        self,
        host: str = "localhost",
        rcon_port: int = DEFAULT_RCON_PORT,
        password: str = "",
        *,
        rcon_uri: str | None = None,
    ) -> None:
        if rcon_uri is not None:
            conn = RconConnection.parse(rcon_uri)
            self.host = conn.host
            self.rcon_port = conn.port
            self.password = conn.password
        else:
            self.host = host
            self.rcon_port = rcon_port
            self.password = password
        self._last_frame: bytes | None = None

    @property
    def connection(self) -> RconConnection:
        """Expose params as a frozen dataclass for introspection / testing."""
        return RconConnection(host=self.host, port=self.rcon_port, password=self.password)

    def reset(self, seed: int | None = None) -> Observation:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def render_frame(self) -> bytes | None:
        # Factorio headless doesn't produce frames — real integration would
        # either spin up a screenshot RCON command (`/screenshot`) or run a
        # client-side observer. Neither is ready.
        return None

    def last_frame(self) -> bytes | None:
        return self._last_frame

    def shutdown(self) -> None:
        return None


__all__ = ["DEFAULT_RCON_PORT", "FactorioEnvironment", "RconConnection"]
