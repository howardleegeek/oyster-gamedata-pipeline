"""Game adapter registry and detection."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Type

if TYPE_CHECKING:
    from bin.games.base_adapter import BaseAdapter

# Re-export registry functions
from bin.games.registry import (
    detect_running_game,
    get_adapter,
    list_supported_games,
    reset_registry,
)

__all__ = [
    "detect_running_game",
    "get_adapter",
    "list_supported_games",
    "reset_registry",
]
