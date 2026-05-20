"""Game adapter registry and detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bin.games.base_adapter import BaseAdapter


def detect_running_game(psutil_process_iter=None) -> "BaseAdapter | None":
    """Detect which game is currently running by scanning processes.

    Args:
        psutil_process_iter: Optional callable to iterate processes.
            Defaults to psutil.process_iter().

    Returns:
        The first matching game adapter instance, or None.
    """
    import psutil

    from bin.games.beamng_adapter import BeamNGAdapter

    # Registry of all game adapters
    _adapters: list[type["BaseAdapter"]] = [BeamNGAdapter]

    if psutil_process_iter is None:
        psutil_process_iter = psutil.process_iter

    try:
        processes = psutil_process_iter(["name", "exe"])
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None

    for adapter_cls in _adapters:
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
