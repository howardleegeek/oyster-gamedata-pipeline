"""Game adapter registry and detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bin.games.base_adapter import BaseAdapter, GameAdapter, GameMetadata, GameSession

# Re-export base classes for convenience
from bin.games.base_adapter import GameAdapter, GameMetadata, GameSession  # noqa: F401
from bin.games.vrchat_adapter import VRChatAdapter  # noqa: F401


def detect_running_game(psutil_process_iter=None) -> "BaseAdapter | GameAdapter | None":
    """Detect which game is currently running by scanning processes.

    Args:
        psutil_process_iter: Optional callable to iterate processes.
            Defaults to psutil.process_iter().

    Returns:
        The first matching game adapter instance, or None.
    """
    import psutil

    from bin.games.beamng_adapter import BeamNGAdapter

    # Registry of all game adapters (GameAdapter protocol)
    _game_adapters: list[type[GameAdapter]] = [VRChatAdapter]

    # Registry of all game adapters (BaseAdapter protocol)
    _base_adapters: list[type[BaseAdapter]] = [BeamNGAdapter]

    if psutil_process_iter is None:
        psutil_process_iter = psutil.process_iter

    # Try GameAdapter protocol first
    for adapter_cls in _game_adapters:
        try:
            instance = adapter_cls()
            session = instance.detect()
            if session is not None:
                return instance
        except Exception:
            continue

    # Try BaseAdapter protocol
    try:
        processes = psutil_process_iter(["name", "exe"])
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None

    for adapter_cls in _base_adapters:
        try:
            for proc in processes:
                try:
                    name = proc.info.get("name") or ""
                    exe = proc.info.get("exe") or ""
                    if adapter_cls.detect(name, exe):
                        return adapter_cls()
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return None


def get_adapter(name: str) -> "GameAdapter | None":
    """Get a game adapter by name.

    Args:
        name: The canonical game name (e.g. 'vrchat', 'roblox').

    Returns:
        The adapter instance, or None if not found.
    """
    _registry: dict[str, type[GameAdapter]] = {
        "vrchat": VRChatAdapter,
    }

    adapter_cls = _registry.get(name.lower())
    if adapter_cls is None:
        return None

    return adapter_cls()
