"""Minecraft environment adapter — STUB.

Integration target: MineRL (https://github.com/minerllabs/minerl) for the
research path (Voyager-style agents, NVIDIA 2023), and Mineflayer
(https://github.com/PrismarineJS/mineflayer) for the headless-bot path
when we need JVM-free operation.

Both are OUT OF SCOPE for this scaffold — they require:
  * MineRL: Java 8 + a Minecraft installation + a proprietary launcher
  * Mineflayer: a running vanilla or Paper server to connect to

Legal: Minecraft has an official modding API (Forge, Fabric) and Mojang's
EULA explicitly permits single-player mods and private servers. Voyager
precedent (NVIDIA 2023) validates this approach for research data
generation. NEVER touch Realms, Hypixel, or any commercial service.
"""

from __future__ import annotations

from typing import Any

from oyster_agent_runner.environments.base import Action, Environment, Observation

_NOT_IMPLEMENTED_MSG = (
    "MinecraftEnvironment is a scaffold stub. Real integration pending: "
    "MineRL (https://github.com/minerllabs/minerl) or Mineflayer "
    "(https://github.com/PrismarineJS/mineflayer). Use MockEnvironment for tests."
)


class MinecraftEnvironment(Environment):
    """Placeholder implementing the `Environment` protocol."""

    def __init__(self, host: str = "localhost", port: int = 25565) -> None:
        self.host = host
        self.port = port

    def reset(self, seed: int | None = None) -> Observation:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def render_frame(self) -> bytes | None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def shutdown(self) -> None:
        # Safe to call on an un-initialized stub.
        return None


__all__ = ["MinecraftEnvironment"]
