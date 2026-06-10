"""BeamNG.drive game adapter.

Detects BeamNG.drive processes, extracts vehicle/map/game_mode metadata
from the game's settings.json, and provides recording hooks that prefer
driving missions while filtering out menu time.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from bin.games.base_adapter import BaseAdapter


class BeamNGAdapter(BaseAdapter):
    """Adapter for BeamNG.drive."""

    GAME_NAME = "beamng"

    # Process names to detect (Windows and Linux)
    _PROCESS_NAMES = ("BeamNG.drive.exe", "BeamNG.drive")

    # Default settings paths per platform
    _DEFAULT_SETTINGS_PATHS = {
        "win32": os.path.join(
            os.path.expanduser("~"),
            "AppData",
            "Local",
            "BeamNG.drive",
            "0.x",
            "settings.json",
        ),
        "linux": os.path.join(
            os.path.expanduser("~"),
            ".local",
            "share",
            "BeamNG.drive",
            "0.x",
            "settings.json",
        ),
    }

    @classmethod
    def detect(cls, process_name: str, process_exe: str) -> bool:
        """Detect if a process belongs to BeamNG.drive.

        Checks both the process name and the executable path for
        BeamNG.drive identifiers.
        """
        name_lower = process_name.lower()
        exe_lower = process_exe.lower()

        for target in cls._PROCESS_NAMES:
            target_lower = target.lower()
            if name_lower == target_lower:
                return True
            if target_lower in exe_lower:
                return True

        return False

    def extract_metadata(self, settings_path: Optional[str] = None) -> Dict[str, Any]:
        """Extract metadata from BeamNG.drive settings.json.

        Looks for vehicle, map, and game_mode information.

        Args:
            settings_path: Optional override path to settings.json.

        Returns:
            Dict with game_name, vehicle, map, game_mode keys.
        """
        metadata: Dict[str, Any] = {
            "game_name": self.GAME_NAME,
            "vehicle": None,
            "map": None,
            "game_mode": None,
        }

        path = settings_path or self._resolve_settings_path()
        if path is None or not os.path.exists(path):
            return metadata

        try:
            data = self._load_json(path)
        except (json.JSONDecodeError, OSError):
            return metadata

        # Extract vehicle info
        vehicle = (
            data.get("vehicle")
            or data.get("lastVehicle")
            or data.get("selectedVehicle")
            or data.get("gameplay", {}).get("vehicle")
        )
        if isinstance(vehicle, dict):
            metadata["vehicle"] = vehicle.get("name") or vehicle.get("id")
        elif isinstance(vehicle, str):
            metadata["vehicle"] = vehicle

        # Extract map info
        game_map = (
            data.get("map")
            or data.get("lastMap")
            or data.get("selectedMap")
            or data.get("gameplay", {}).get("map")
            or data.get("level")
        )
        if isinstance(game_map, dict):
            metadata["map"] = game_map.get("name") or game_map.get("id")
        elif isinstance(game_map, str):
            metadata["map"] = game_map

        # Extract game mode
        game_mode = (
            data.get("gameMode")
            or data.get("game_mode")
            or data.get("gamemode")
            or data.get("gameplay", {}).get("mode")
            or data.get("mode")
        )
        if isinstance(game_mode, dict):
            metadata["game_mode"] = game_mode.get("name") or game_mode.get("id")
        elif isinstance(game_mode, str):
            metadata["game_mode"] = game_mode

        return metadata

    def get_recording_hooks(self) -> List[Dict[str, Any]]:
        """Return recording hooks that prefer driving missions.

        Filters out menu/lobby time to only record actual gameplay.
        """
        return [
            {
                "name": "beamng_driving_mission",
                "event": "on_mission_start",
                "filter_fn": "prefer_driving_missions",
                "description": "Record only during active driving missions",
            },
            {
                "name": "beamng_filter_menu",
                "event": "on_state_change",
                "filter_fn": "filter_out_menu_time",
                "description": "Skip recording during menus and loading screens",
            },
            {
                "name": "beamng_vehicle_spawn",
                "event": "on_vehicle_spawn",
                "filter_fn": "track_vehicle_changes",
                "description": "Tag recordings with vehicle metadata on spawn",
            },
        ]

    def _resolve_settings_path(self) -> Optional[str]:
        """Resolve the settings.json path for the current platform."""
        import sys

        platform = sys.platform
        if platform == "win32":
            return self._DEFAULT_SETTINGS_PATHS.get("win32")
        elif platform == "linux":
            return self._DEFAULT_SETTINGS_PATHS.get("linux")
        # Fallback: try both
        for path in self._DEFAULT_SETTINGS_PATHS.values():
            if os.path.exists(path):
                return path
        return None
