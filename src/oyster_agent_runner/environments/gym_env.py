"""Gymnasium / OpenAI-Gym environment adapter — STUB.

Integration target: `gymnasium` (the maintained fork of OpenAI Gym,
https://gymnasium.farama.org). Classic control, Atari (via
`ale-py`), Procgen, MineRL (registered as gym envs), and MiniGrid
are all drop-in once this adapter is implemented.

Real integration is a thin wrapper:
    env = gymnasium.make(env_id)
    obs, info = env.reset(seed=seed)
    obs, r, terminated, truncated, info = env.step(action)

NOT installed here — gymnasium pulls in numpy/pygame/Box2D etc.
Production deployments should `pip install "gymnasium[classic-control]"`
(or the relevant extra) alongside this package.
"""

from __future__ import annotations

from typing import Any

from oyster_agent_runner.environments.base import Action, Environment, Observation

_NOT_IMPLEMENTED_MSG = (
    "GymEnvironment is a scaffold stub. Real integration pending: "
    "`pip install gymnasium[extras]` and wrap `gymnasium.make(env_id)`. "
    "Use MockEnvironment for tests."
)


class GymEnvironment(Environment):
    """Placeholder implementing the `Environment` protocol.

    Constructor takes the gymnasium env id (e.g. 'MountainCarContinuous-v0',
    'ALE/Breakout-v5') so the real implementation can be swapped in without
    touching caller code.
    """

    def __init__(self, env_id: str) -> None:
        self.env_id = env_id

    def reset(self, seed: int | None = None) -> Observation:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def render_frame(self) -> bytes | None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def shutdown(self) -> None:
        return None


__all__ = ["GymEnvironment"]
