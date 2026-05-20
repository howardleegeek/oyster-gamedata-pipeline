"""Game adapter registry — discovery and top-level helpers.

Usage::

    from bin.games import detect_running_game

    adapter = detect_running_game()
    if adapter is not None:
        session = adapter.detect()
        if session:
            meta = adapter.extract_metadata(session.pid)
"""

from __future__ import annotations

from typing import Optional

from bin.games.base_adapter import GameAdapter, GameMetadata, GameSession
from bin.games.roblox_adapter import RobloxAdapter

# Ordered list of adapter classes to try during discovery.
# New adapters should be appended here.
_ADAPTERS: list[type[GameAdapter]] = [
    RobloxAdapter,
]


def detect_running_game() -> Optional[GameAdapter]:
    """Iterate through registered adapters and return the first one whose
    ``detect()`` method finds a running game process.

    Returns ``None`` if no known game is currently running.
    """
    for adapter_cls in _ADAPTERS:
        adapter = adapter_cls()
        session = adapter.detect()
        if session is not None:
            return adapter
    return None


def get_adapter(game_name: str) -> Optional[GameAdapter]:
    """Return an adapter instance by canonical game name, or ``None``."""
    for adapter_cls in _ADAPTERS:
        inst = adapter_cls()
        if inst.game_name == game_name:
            return inst
    return None


__all__ = [
    "GameAdapter",
    "GameMetadata",
    "GameSession",
    "RobloxAdapter",
    "detect_running_game",
    "get_adapter",
]
