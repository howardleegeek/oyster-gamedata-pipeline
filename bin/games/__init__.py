"""Game adapter registry and detection."""

from __future__ import annotations

from bin.games.base_adapter import BaseAdapter, GameAdapter, GameMetadata, GameSession
from bin.games.beamng_adapter import BeamNGAdapter
from bin.games.mc_adapter import MinecraftAdapter
from bin.games.registry import (
    detect_running_game,
    list_supported_games,
    reset_registry,
)
from bin.games.registry import (
    get_adapter as _get_adapter_class,
)
from bin.games.roblox_adapter import RobloxAdapter
from bin.games.vrchat_adapter import VRChatAdapter


def get_adapter(name: str) -> BaseAdapter | GameAdapter | None:
    """Return an adapter instance by canonical game name."""
    adapter_cls = _get_adapter_class(name)
    if adapter_cls is None:
        return None
    return adapter_cls()


__all__ = [
    "BaseAdapter",
    "BeamNGAdapter",
    "GameAdapter",
    "GameMetadata",
    "GameSession",
    "MinecraftAdapter",
    "RobloxAdapter",
    "VRChatAdapter",
    "detect_running_game",
    "get_adapter",
    "list_supported_games",
    "reset_registry",
]
