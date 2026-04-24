"""Minecraft environment adapter — STUB with both integration paths stubbed.

Two target integrations, each with different tradeoffs. Pick based on
whether you need *research repeatability* or *live-world generality*:

Path A — MineRL (research Gym env)
----------------------------------
Repo: https://github.com/minerllabs/minerl
Pros:
  * Gym-compatible API — `env = minerl.env.make('MineRLObtainDiamond-v0')`
  * Pixel observations baked in (POV from a real render window)
  * Frozen action/obs spaces → reproducible benchmarks (NeurIPS MineRL)
  * Voyager / VPT papers targeted this
Cons:
  * Requires Java 8 + a Minecraft installation + a proprietary launcher
  * Heavy binary install — not pip-only
  * Action space quantized; can't express arbitrary mouse moves at full fidelity

Use MineRL when:
  * You want to compare against published agent benchmarks
  * You need pixel observations for vision-based agents
  * You can afford the heavyweight install

Path B — Mineflayer (headless Node.js bot)
------------------------------------------
Repo: https://github.com/PrismarineJS/mineflayer
Pros:
  * Pure JS — no JVM, no proprietary launcher
  * Full protocol access (block-level read/write, inventory, crafting, chat)
  * Connects to any vanilla/Paper/Spigot server
  * Best for long-horizon agentic gameplay (Voyager-style skill library)
Cons:
  * NO pixel observations (bot observes the protocol, not rendered frames)
  * Requires a running server (either local or private remote)
  * Node.js subprocess → IPC bridge needed from Python

Use Mineflayer when:
  * You need arbitrary server interaction (multiplayer research, co-op)
  * You don't need vision — symbolic observations are fine
  * You want headless operation in CI

Runtime status
--------------
Both paths are OUT OF SCOPE for this scaffold:
  * MineRL: requires Java 8 + Minecraft install + launcher credentials
  * Mineflayer: requires Node.js runtime + a running server to connect to

The `path` kwarg selects which integration this adapter *will* route to
when the backend is implemented. It's stored as an attribute so tests
can verify the param reaches the real wrapper unchanged.

Legal: Minecraft has an official modding API (Forge, Fabric) and Mojang's
EULA explicitly permits single-player mods and private servers. Voyager
precedent (NVIDIA 2023) validates this approach for research data
generation. NEVER touch Realms, Hypixel, or any commercial service.
"""

from __future__ import annotations

from typing import Any, Literal

from oyster_agent_runner.environments.base import Action, Environment, Observation

MinecraftPath = Literal["minerl", "mineflayer"]

_NOT_IMPLEMENTED_MSG = (
    "MinecraftEnvironment is a scaffold stub. Real integration pending: "
    "MineRL (https://github.com/minerllabs/minerl) or Mineflayer "
    "(https://github.com/PrismarineJS/mineflayer). Use MockEnvironment for tests."
)


class MinecraftEnvironment(Environment):
    """Placeholder implementing the `Environment` protocol.

    Parameters
    ----------
    path:
        Which backend to route to when the real integration ships.
        `"minerl"` (pixel obs, research benchmark) or `"mineflayer"`
        (symbolic obs, arbitrary server). Default: `"mineflayer"`.
    host / port:
        Server connection params — only used for the `mineflayer` path.
        MineRL spawns its own integrated server and ignores these.
    minerl_env_id:
        When `path="minerl"`, the registered Gym env id to instantiate,
        e.g. `'MineRLObtainDiamond-v0'` or `'MineRLBasaltFindCave-v0'`.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 25565,
        *,
        path: MinecraftPath = "mineflayer",
        minerl_env_id: str = "MineRLObtainDiamond-v0",
    ) -> None:
        self.host = host
        self.port = port
        self.path: MinecraftPath = path
        self.minerl_env_id = minerl_env_id
        self._last_frame: bytes | None = None

    def reset(self, seed: int | None = None) -> Observation:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def render_frame(self) -> bytes | None:
        # MineRL produces pixel frames; Mineflayer does not. Real wrapper
        # will branch on `self.path`.
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def last_frame(self) -> bytes | None:
        return self._last_frame

    def shutdown(self) -> None:
        # Safe to call on an un-initialized stub.
        return None


__all__ = ["MinecraftEnvironment", "MinecraftPath"]
