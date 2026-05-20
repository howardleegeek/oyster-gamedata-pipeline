"""Roblox game adapter.

Detects a running Roblox client by process name (``RobloxPlayerBeta.exe``
on Windows, ``RobloxPlayer.app`` on macOS) and extracts ``place_id`` /
``universe_id`` from the local Roblox log files.
"""

from __future__ import annotations

import logging
import os
import platform
import re
from pathlib import Path
from typing import Any, Dict, Optional

from bin.games.base_adapter import BaseAdapter

logger = logging.getLogger(__name__)

# Process names used to identify a running Roblox client.
_ROBLOX_EXE_WIN = "RobloxPlayerBeta.exe"
_ROBLOX_EXE_MAC = "RobloxPlayer.app"

# Regex patterns for extracting IDs from Roblox local logs.
_PLACE_ID_RE = re.compile(r"place[_-]?id\s*[=:]\s*(\d+)", re.IGNORECASE)
_UNIVERSE_ID_RE = re.compile(r"universe[_-]?id\s*[=:]\s*(\d+)", re.IGNORECASE)


def _roblox_exe_names() -> tuple[str, ...]:
    """Return all platform-specific Roblox executable names."""
    return (_ROBLOX_EXE_WIN, _ROBLOX_EXE_MAC)


def _roblox_log_dir() -> Path:
    """Return the platform-specific Roblox log directory."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Roblox" / "logs"
    # macOS / Linux fallback
    return Path.home() / "Library" / "Logs" / "Roblox"


def _extract_ids_from_logs(log_dir: Path) -> dict[str, str]:
    """Scan Roblox log files for place_id and universe_id.

    Returns a dict with keys ``place_id`` and ``universe_id`` (empty
    strings if not found).
    """
    place_id = ""
    universe_id = ""

    if not log_dir.is_dir():
        return {"place_id": place_id, "universe_id": universe_id}

    try:
        log_files = sorted(
            log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except OSError:
        return {"place_id": place_id, "universe_id": universe_id}

    for log_file in log_files[:5]:  # check the 5 most recent logs
        try:
            content = log_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        m_place = _PLACE_ID_RE.search(content)
        m_universe = _UNIVERSE_ID_RE.search(content)

        if m_place and not place_id:
            place_id = m_place.group(1)
        if m_universe and not universe_id:
            universe_id = m_universe.group(1)

        if place_id and universe_id:
            break

    return {"place_id": place_id, "universe_id": universe_id}


class RobloxAdapter(BaseAdapter):
    """Adapter for the Roblox game client."""

    GAME_NAME = "roblox"

    @classmethod
    def detect(cls, process_name: str, process_exe: str) -> bool:
        """Detect if a process belongs to Roblox.

        Checks both the process name and the executable path for
        Roblox identifiers.
        """
        name_lower = process_name.lower()
        exe_lower = process_exe.lower()

        for target in _roblox_exe_names():
            target_lower = target.lower()
            if name_lower == target_lower:
                return True
            if target_lower in exe_lower:
                return True

        return False

    def extract_metadata(self, settings_path: Optional[str] = None) -> Dict[str, Any]:
        """Extract Roblox metadata (place_id, universe_id) from local logs.

        Args:
            settings_path: Optional override path to Roblox log directory.

        Returns:
            Dict with game_name, place_id, universe_id keys.
        """
        metadata: Dict[str, Any] = {
            "game_name": self.GAME_NAME,
            "place_id": None,
            "universe_id": None,
        }

        log_dir = Path(settings_path) if settings_path else _roblox_log_dir()
        ids = _extract_ids_from_logs(log_dir)

        if ids["place_id"]:
            metadata["place_id"] = ids["place_id"]
        if ids["universe_id"]:
            metadata["universe_id"] = ids["universe_id"]

        return metadata

    def get_recording_hooks(self) -> list[Dict[str, Any]]:
        """Return recording hooks for Roblox sessions."""
        return [
            {
                "name": "roblox_place_join",
                "event": "on_place_load",
                "filter_fn": "capture_place_info",
                "description": "Record place_id and universe_id on game join",
            },
            {
                "name": "roblox_overlay_marker",
                "event": "pre_record",
                "filter_fn": "inject_overlay",
                "description": "Inject 'Recording for Oyster' overlay marker",
            },
            {
                "name": "roblox_filter_menu",
                "event": "on_state_change",
                "filter_fn": "filter_menu_time",
                "description": "Skip recording during Roblox menu screens",
            },
        ]
