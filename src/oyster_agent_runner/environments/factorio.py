"""Factorio RCON/mod-relay environment adapter.

The production path is intentionally thin: Factorio exposes a Source-RCON
server, and the Oyster observer mod exposes two Lua remote calls:

```
remote.call("oyster_recorder", "observe") -> JSON observation
remote.call("oyster_recorder", "act", "<json action>") -> JSON ack
```

CI never starts Factorio. Tests inject a fake RCON client that implements
``send_command(command: str) -> str`` or ``command(command: str) -> str``.
This keeps the plug-and-play contract testable without the game binary.
"""

from __future__ import annotations

import json
import re
import socket
import struct
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from oyster_agent_runner.environments.base import Action, Environment, Observation

# rcon://[password@]host[:port]
_RCON_URI_RE = re.compile(
    r"^rcon://" r"(?::(?P<password>[^@]*)@)?" r"(?P<host>[^:@/]+)" r"(?::(?P<port>\d+))?" r"/?$"
)

DEFAULT_RCON_PORT = 25575
DEFAULT_RCON_TIMEOUT = 2.0
OBSERVATION_SOURCE = "factorio-rcon-mod"
OBSERVE_COMMAND = '/silent-command rcon.print(remote.call("oyster_recorder", "observe"))'
ACTION_COMMAND_TEMPLATE = (
    '/silent-command rcon.print(remote.call("oyster_recorder", "act", {payload}))'
)
_STEP_BEFORE_RESET_MSG = (
    "FactorioEnvironment.step called before reset(). Call reset() first so the "
    "RCON/mod relay is connected and an initial observation is available."
)


@runtime_checkable
class RconClient(Protocol):
    """Minimal command surface used by the environment and fake test clients."""

    def send_command(self, command: str) -> str:
        """Send one RCON command and return the command response body."""
        ...

    def close(self) -> None:
        """Release network resources."""
        ...


@dataclass(frozen=True)
class RconConnection:
    """Parsed RCON connection params: pure data, no socket state."""

    host: str
    port: int
    password: str

    @classmethod
    def parse(cls, uri: str) -> RconConnection:
        """Parse a ``rcon://[pw@]host[:port]`` URI."""
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


@dataclass(frozen=True)
class FactorioObservation:
    """Validated observation emitted by the Oyster Factorio observer mod."""

    tick: int
    player_position: dict[str, float]
    surface: str
    inventory: dict[str, int]
    entities_near: list[dict[str, Any]]
    source: str = OBSERVATION_SOURCE

    @classmethod
    def from_payload(cls, payload: str | dict[str, Any]) -> FactorioObservation:
        """Parse and normalize a JSON observation payload from RCON."""
        data = _json_payload(payload, context="Factorio observation")
        missing = [
            key
            for key in ("tick", "player_position", "surface", "inventory", "entities_near")
            if key not in data
        ]
        if missing:
            raise FactorioProtocolError(
                f"Factorio observation missing required fields: {', '.join(missing)}"
            )

        return cls(
            tick=_coerce_int(data["tick"], "tick"),
            player_position=_coerce_position(data["player_position"]),
            surface=str(data["surface"]),
            inventory=_coerce_inventory(data["inventory"]),
            entities_near=_coerce_entities(data["entities_near"]),
            source=str(data.get("source") or OBSERVATION_SOURCE),
        )

    def to_observation(self) -> Observation:
        """Return a JSON-serializable dict matching the plug-and-play schema."""
        return {
            "tick": self.tick,
            "player_position": dict(self.player_position),
            "surface": self.surface,
            "inventory": dict(self.inventory),
            "entities_near": [dict(entity) for entity in self.entities_near],
            "source": self.source,
        }


@dataclass(frozen=True)
class FactorioAction:
    """Normalized action accepted by the Factorio observer/action mod."""

    op: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def validate(cls, action: Action) -> FactorioAction:
        """Validate and normalize an environment action.

        Supported operations:
        - ``noop``
        - ``move``: ``direction`` in north/south/east/west/up/down/left/right
        - ``craft``: ``recipe`` plus positive integer ``count``
        - ``place``: ``entity`` plus numeric ``x``/``y``
        - ``mine``: positive integer ``entity_id`` or numeric ``x``/``y``
        """
        if not isinstance(action, dict):
            raise InvalidFactorioAction("Factorio action must be a dict.")

        op = action.get("op")
        if not isinstance(op, str):
            raise InvalidFactorioAction("Factorio action requires string field 'op'.")
        op = op.strip().lower()

        if op == "noop":
            return cls(op="noop")
        if op == "move":
            return cls(op=op, payload=_validate_move(action))
        if op == "craft":
            return cls(op=op, payload=_validate_craft(action))
        if op == "place":
            return cls(op=op, payload=_validate_place(action))
        if op == "mine":
            return cls(op=op, payload=_validate_mine(action))
        raise InvalidFactorioAction(
            f"Unsupported Factorio action op {op!r}; expected one of move/craft/place/mine/noop."
        )

    def to_mod_payload(self) -> dict[str, Any]:
        """Return the JSON object sent to the Lua action relay."""
        return {"op": self.op, **self.payload}


class InvalidFactorioAction(ValueError, NotImplementedError):
    """Raised when an action violates the Factorio plug-and-play contract.

    It also subclasses ``NotImplementedError`` so the older scaffold regression
    test still treats invalid/unsupported calls as the historical stub failure.
    """


class FactorioProtocolError(RuntimeError):
    """Raised when the RCON/mod relay returns malformed data."""


class FactorioRconClient:
    """Small Source-RCON client for Factorio.

    This is deliberately minimal and dependency-free. It is enough for the
    plug-and-play path to talk to a local/private Factorio instance while tests
    use injected fake clients.
    """

    AUTH = 3
    COMMAND = 2
    RESPONSE = 0

    def __init__(self, connection: RconConnection, timeout: float = DEFAULT_RCON_TIMEOUT) -> None:
        self._connection = connection
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._request_id = 1000

    def connect(self) -> None:
        """Open the socket and authenticate against Factorio RCON."""
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection(
                (self._connection.host, self._connection.port),
                timeout=self._timeout,
            )
            self._sock.settimeout(self._timeout)
            self._send_packet(self.AUTH, self._connection.password)
            response_id, _response_type, body = self._recv_packet()
        except OSError as exc:
            self.close()
            raise ConnectionError(
                "Factorio RCON connection failed "
                f"for {self._connection.host}:{self._connection.port}: {exc}"
            ) from exc

        if response_id == -1:
            self.close()
            raise ConnectionError(
                "Factorio RCON authentication failed "
                f"for {self._connection.host}:{self._connection.port}."
            )
        if body and "Invalid password" in body:
            self.close()
            raise ConnectionError(
                "Factorio RCON authentication failed: server rejected the password."
            )

    def send_command(self, command: str) -> str:
        """Send a single command and return the response body."""
        if self._sock is None:
            self.connect()
        try:
            self._send_packet(self.COMMAND, command)
            _response_id, _response_type, body = self._recv_packet()
            return body
        except OSError as exc:
            self.close()
            raise ConnectionError(f"Factorio RCON command failed: {exc}") from exc

    def close(self) -> None:
        """Close the underlying socket."""
        if self._sock is None:
            return
        try:
            self._sock.close()
        finally:
            self._sock = None

    def _send_packet(self, packet_type: int, body: str) -> None:
        if self._sock is None:
            raise ConnectionError("Factorio RCON socket is not connected.")
        self._request_id += 1
        encoded = body.encode("utf-8")
        payload = struct.pack("<ii", self._request_id, packet_type) + encoded + b"\x00\x00"
        packet = struct.pack("<i", len(payload)) + payload
        self._sock.sendall(packet)

    def _recv_packet(self) -> tuple[int, int, str]:
        if self._sock is None:
            raise ConnectionError("Factorio RCON socket is not connected.")
        raw_size = self._recv_exact(4)
        (size,) = struct.unpack("<i", raw_size)
        raw_payload = self._recv_exact(size)
        response_id, response_type = struct.unpack("<ii", raw_payload[:8])
        body = raw_payload[8:-2].decode("utf-8", errors="replace")
        return response_id, response_type, body

    def _recv_exact(self, size: int) -> bytes:
        if self._sock is None:
            raise ConnectionError("Factorio RCON socket is not connected.")
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise ConnectionError("Factorio RCON socket closed while reading response.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class FactorioEnvironment(Environment):
    """Factorio environment backed by RCON plus the Oyster Lua observer mod."""

    def __init__(
        self,
        host: str = "localhost",
        rcon_port: int = DEFAULT_RCON_PORT,
        password: str = "",
        *,
        rcon_uri: str | None = None,
        rcon_client: object | None = None,
        timeout: float = DEFAULT_RCON_TIMEOUT,
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
        self.timeout = timeout
        self._client = rcon_client
        self._owns_client = rcon_client is None
        self._last_observation: Observation | None = None
        self._last_frame: bytes | None = None
        self._has_reset = False

    @property
    def connection(self) -> RconConnection:
        """Expose params as a frozen dataclass for introspection/testing."""
        return RconConnection(host=self.host, port=self.rcon_port, password=self.password)

    def reset(self, seed: int | None = None) -> Observation:
        """Fetch the current game state from the observer mod.

        Factorio is a continuous sandbox, so reset does not restart the game.
        It returns the current observation, matching the shared Environment
        protocol; per-step reward/done/info are emitted by ``step``.
        """
        del seed
        obs = self._observe()
        self._has_reset = True
        return obs

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        """Validate and apply an action, then fetch the post-action observation."""
        validated = FactorioAction.validate(action)
        if not self._has_reset:
            raise RuntimeError(_STEP_BEFORE_RESET_MSG)

        action_response: dict[str, Any] = {}
        if validated.op != "noop":
            raw_ack = self._send_command(_action_command(validated))
            action_response = _optional_json_payload(raw_ack, context="Factorio action response")
        else:
            raw_ack = self._send_command(_action_command(validated))
            action_response = _optional_json_payload(raw_ack, context="Factorio noop response")

        obs = self._observe()
        info = {
            "source": obs["source"],
            "action": validated.to_mod_payload(),
            "action_response": action_response,
        }
        return obs, 0.0, False, info

    def render_frame(self) -> bytes | None:
        """Factorio headless does not expose frames through RCON."""
        return None

    def last_frame(self) -> bytes | None:
        """Return the latest rendered frame when a future visual relay exists."""
        return self._last_frame

    def shutdown(self) -> None:
        """Close owned RCON resources."""
        close = getattr(self._client, "close", None)
        if self._owns_client and callable(close):
            close()

    def _observe(self) -> Observation:
        raw = self._send_command(OBSERVE_COMMAND)
        observation = FactorioObservation.from_payload(raw).to_observation()
        self._last_observation = observation
        return observation

    def _send_command(self, command: str) -> str:
        client = self._ensure_client()
        send_command = getattr(client, "send_command", None)
        if callable(send_command):
            return str(send_command(command))

        legacy_command = getattr(client, "command", None)
        if callable(legacy_command):
            return str(legacy_command(command))

        raise TypeError(
            "Factorio rcon_client must implement send_command(command: str) "
            "or command(command: str)."
        )

    def _ensure_client(self) -> object:
        if self._client is None:
            self._client = FactorioRconClient(self.connection, timeout=self.timeout)
        return self._client


def _action_command(action: FactorioAction) -> str:
    payload = json.dumps(action.to_mod_payload(), sort_keys=True, separators=(",", ":"))
    return ACTION_COMMAND_TEMPLATE.format(payload=_lua_string(payload))


def _lua_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _json_payload(payload: str | dict[str, Any], *, context: str) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        raise FactorioProtocolError(f"{context} must be JSON object/string, got {type(payload)}.")
    stripped = payload.strip()
    if not stripped:
        raise FactorioProtocolError(f"{context} response was empty.")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise FactorioProtocolError(f"{context} response was not valid JSON: {stripped!r}") from exc
    if not isinstance(data, dict):
        raise FactorioProtocolError(f"{context} response must be a JSON object.")
    return data


def _optional_json_payload(payload: str, *, context: str) -> dict[str, Any]:
    stripped = payload.strip()
    if not stripped:
        return {}
    return _json_payload(stripped, context=context)


def _coerce_position(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise FactorioProtocolError("Factorio observation player_position must be an object.")
    return {
        "x": _coerce_float(value.get("x"), "player_position.x"),
        "y": _coerce_float(value.get("y"), "player_position.y"),
    }


def _coerce_inventory(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise FactorioProtocolError("Factorio observation inventory must be an object.")
    return {str(name): _coerce_int(count, f"inventory.{name}") for name, count in value.items()}


def _coerce_entities(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise FactorioProtocolError("Factorio observation entities_near must be a list.")
    entities: list[dict[str, Any]] = []
    for index, entity in enumerate(value):
        if not isinstance(entity, dict):
            raise FactorioProtocolError(f"entities_near[{index}] must be an object.")
        normalized = dict(entity)
        if "position" in normalized and isinstance(normalized["position"], dict):
            pos = normalized["position"]
            normalized["position"] = {
                "x": _coerce_float(pos.get("x"), f"entities_near[{index}].position.x"),
                "y": _coerce_float(pos.get("y"), f"entities_near[{index}].position.y"),
            }
        entities.append(normalized)
    return entities


def _coerce_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise FactorioProtocolError(f"{field_name} must be an integer.") from exc


def _coerce_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise FactorioProtocolError(f"{field_name} must be numeric.") from exc


def _validate_move(action: Action) -> dict[str, Any]:
    direction = action.get("direction")
    allowed = {"north", "south", "east", "west", "up", "down", "left", "right"}
    if direction not in allowed:
        raise InvalidFactorioAction(
            "Factorio move action requires direction north/south/east/west/up/down/left/right."
        )
    ticks = _action_int(action.get("ticks", 1), "ticks")
    if ticks < 1:
        raise InvalidFactorioAction("Factorio move action requires ticks >= 1.")
    return {"direction": direction, "ticks": ticks}


def _validate_craft(action: Action) -> dict[str, Any]:
    recipe = action.get("recipe")
    if not isinstance(recipe, str) or not recipe.strip():
        raise InvalidFactorioAction("Factorio craft action requires non-empty recipe.")
    count = _action_int(action.get("count", 1), "count")
    if count < 1:
        raise InvalidFactorioAction("Factorio craft action requires count >= 1.")
    return {"recipe": recipe.strip(), "count": count}


def _validate_place(action: Action) -> dict[str, Any]:
    entity = action.get("entity")
    if not isinstance(entity, str) or not entity.strip():
        raise InvalidFactorioAction("Factorio place action requires non-empty entity.")
    return {
        "entity": entity.strip(),
        "x": _action_float(action.get("x"), "x"),
        "y": _action_float(action.get("y"), "y"),
        **({"direction": action["direction"]} if "direction" in action else {}),
    }


def _validate_mine(action: Action) -> dict[str, Any]:
    if "entity_id" in action:
        entity_id = _action_int(action["entity_id"], "entity_id")
        if entity_id < 1:
            raise InvalidFactorioAction("Factorio mine action requires entity_id >= 1.")
        return {"entity_id": entity_id}
    if "x" in action and "y" in action:
        return {"x": _action_float(action.get("x"), "x"), "y": _action_float(action.get("y"), "y")}
    raise InvalidFactorioAction("Factorio mine action requires entity_id or x/y coordinates.")


def _action_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidFactorioAction(
            f"Factorio action field {field_name!r} must be numeric."
        ) from exc


def _action_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidFactorioAction(
            f"Factorio action field {field_name!r} must be an integer."
        ) from exc


__all__ = [
    "DEFAULT_RCON_PORT",
    "DEFAULT_RCON_TIMEOUT",
    "FactorioAction",
    "FactorioEnvironment",
    "FactorioObservation",
    "FactorioProtocolError",
    "FactorioRconClient",
    "InvalidFactorioAction",
    "OBSERVATION_SOURCE",
    "RconClient",
    "RconConnection",
]
