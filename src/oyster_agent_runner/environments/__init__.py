"""Environment plugins for the agent runner.

Each sub-module adapts an external simulator (Minecraft via MineRL,
Factorio via RCON, OpenAI Gym, etc.) to the `Environment` protocol in
`base`. See the per-file docstrings for integration status.
"""

from oyster_agent_runner.environments.base import (
    Action,
    Environment,
    MockEnvironment,
    Observation,
)

__all__ = [
    "Action",
    "Environment",
    "MockEnvironment",
    "Observation",
]
