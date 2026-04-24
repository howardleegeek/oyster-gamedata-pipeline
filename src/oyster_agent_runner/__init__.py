"""Oyster Labs Layer 4 — LLM-agent-driven gameplay data generation.

This package drives LLM agents through task-directed scenarios in mod-friendly
games and emits labeled trajectories in a JSONL format compatible with
`oyster-enrichment`'s input-log event schema.

Legal red line: ONLY mod-friendly single-player / sandbox environments
(Minecraft, Factorio, Skyrim SE, single-player GTA V via ScriptHookV,
Civilization VI, OpenAI Gym / MineRL / Procgen). NEVER Activision / Riot /
Epic commercial online games.
"""

from oyster_agent_runner.schema import (
    AgentTask,
    TaskResult,
    TrajectoryEntry,
    TrajectoryEvent,
)

__all__ = [
    "AgentTask",
    "TaskResult",
    "TrajectoryEntry",
    "TrajectoryEvent",
]

__version__ = "0.1.0"
