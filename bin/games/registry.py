"""Central game adapter registry.

Auto-discovers all ``*_adapter.py`` modules in ``bin/games/``,
instantiates each adapter class, and provides detection/listing helpers.
"""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Type

if TYPE_CHECKING:
    from bin.games.base_adapter import BaseAdapter

# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

_GAMES_DIR = Path(__file__).resolve().parent

# Cache so we only scan once per process
_registry: Optional[List[Type["BaseAdapter"]]] = None


def _discover_adapters() -> List[Type["BaseAdapter"]]:
    """Scan ``bin/games/`` for ``*_adapter.py`` modules and collect adapter classes.

    Returns a list of adapter *classes* (not instances) that subclass
    ``BaseAdapter`` and have a non-empty ``GAME_NAME``.
    """
    from bin.games.base_adapter import BaseAdapter

    adapters: List[Type["BaseAdapter"]] = []
    seen_names: set[str] = set()

    for importer, modname, ispkg in pkgutil.iter_modules([str(_GAMES_DIR)]):
        # Only consider *_adapter.py files (skip base_adapter.py)
        if not modname.endswith("_adapter"):
            continue
        if modname == "base_adapter":
            continue

        try:
            module = importlib.import_module(f"bin.games.{modname}")
        except Exception:
            # Skip modules that fail to import (missing deps, etc.)
            continue

        # Find all classes in the module that subclass BaseAdapter
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseAdapter)
                and obj is not BaseAdapter
                and getattr(obj, "GAME_NAME", "")
                and obj.GAME_NAME not in seen_names
            ):
                adapters.append(obj)
                seen_names.add(obj.GAME_NAME)

    # Sort by GAME_NAME for deterministic ordering
    adapters.sort(key=lambda cls: cls.GAME_NAME)
    return adapters


def _get_registry() -> List[Type["BaseAdapter"]]:
    """Return the cached list of adapter classes, discovering on first call."""
    global _registry
    if _registry is None:
        _registry = _discover_adapters()
    return _registry


def reset_registry() -> None:
    """Clear the cached registry. Useful for testing."""
    global _registry
    _registry = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_running_game(psutil_process_iter=None) -> Optional["BaseAdapter"]:
    """Detect which game is currently running by scanning processes.

    Iterates over all registered adapters and returns the first one whose
    ``detect()`` method matches a running process.

    Args:
        psutil_process_iter: Optional callable to iterate processes.
            Defaults to ``psutil.process_iter``.

    Returns:
        The first matching game adapter *instance*, or ``None``.
    """
    import psutil

    registry = _get_registry()

    if psutil_process_iter is None:
        psutil_process_iter = psutil.process_iter

    try:
        processes = list(psutil_process_iter(["name", "exe"]))
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None

    for adapter_cls in registry:
        for proc in processes:
            try:
                name = proc.info.get("name") or ""
                exe = proc.info.get("exe") or ""
                if adapter_cls.detect(name, exe):
                    return adapter_cls()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

    return None


def list_supported_games() -> List[str]:
    """Return a sorted list of supported game names.

    Returns:
        List of ``GAME_NAME`` strings from all registered adapters.
    """
    registry = _get_registry()
    return [cls.GAME_NAME for cls in registry]


def get_adapter(game_name: str) -> Optional[Type["BaseAdapter"]]:
    """Look up an adapter class by its ``GAME_NAME``.

    Args:
        game_name: The game name to look up (case-sensitive).

    Returns:
        The adapter class, or ``None`` if not found.
    """
    registry = _get_registry()
    for cls in registry:
        if cls.GAME_NAME == game_name:
            return cls
    return None
