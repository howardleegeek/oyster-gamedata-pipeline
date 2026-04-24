"""Factorio environment adapter — STUB.

Integration target: Factorio RCON over TCP + the official modding API.

Real integration requires:
  * A Factorio server binary (the game ships with `factorio --server`)
  * A mod that exposes world state + accepts structured commands
  * An RCON client (e.g. `rcon` pip package or raw socket)

Legal: Factorio has an explicit modding API and the developer (Wube)
endorses headless-server research. Single-player and private multiplayer
only — NEVER touch public community servers without consent.
"""

from __future__ import annotations

from typing import Any

from oyster_agent_runner.environments.base import Action, Environment, Observation

_NOT_IMPLEMENTED_MSG = (
    "FactorioEnvironment is a scaffold stub. Real integration pending: "
    "Factorio RCON + modding API. Use MockEnvironment for tests."
)


class FactorioEnvironment(Environment):
    """Placeholder implementing the `Environment` protocol."""

    def __init__(self, host: str = "localhost", rcon_port: int = 25575, password: str = "") -> None:
        self.host = host
        self.rcon_port = rcon_port
        self.password = password

    def reset(self, seed: int | None = None) -> Observation:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def render_frame(self) -> bytes | None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def shutdown(self) -> None:
        return None


__all__ = ["FactorioEnvironment"]
