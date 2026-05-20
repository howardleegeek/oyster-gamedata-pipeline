"""VRChat game adapter.

Detects a running VRChat process and extracts avatar/world metadata
from the game's configuration files.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from bin.games.base_adapter import BaseAdapter


class VRChatAdapter(BaseAdapter):
    """Adapter for VRChat."""

    GAME_NAME = "vrchat"

    # Process names to detect
    _PROCESS_NAMES = ("VRChat.exe", "vrchat")

    # Default VRChat config paths per platform
    _DEFAULT_CONFIG_PATHS = {
        "win32": os.path.join(
            os.path.expanduser("~"),
            "AppData",
            "LocalLow",
            "VRChat",
            "VRChat",
        ),
        "linux": os.path.join(
            os.path.expanduser("~"),
            ".local",
            "share",
            "Steam",
            "steamapps",
            "compatdata",
            "438100",
            "pfx",
            "drive_c",
            "users",
            "steamuser",
            "AppData",
            "LocalLow",
            "VRChat",
            "VRChat",
        ),
    }

    @classmethod
    def detect(cls, process_name: str, process_exe: str) -> bool:
        """Detect if a process belongs to VRChat.

        Checks both the process name and the executable path for
        VRChat identifiers.
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
        """Extract metadata from VRChat config files.

        Looks for current avatar and world information.

        Args:
            settings_path: Optional override path to VRChat config directory.

        Returns:
            Dict with game_name, current_avatar, current_world, instance_id keys.
        """
        metadata: Dict[str, Any] = {
            "game_name": self.GAME_NAME,
            "current_avatar": None,
            "current_world": None,
            "instance_id": None,
        }

        config_dir = settings_path or self._resolve_config_path()
        if config_dir is None or not os.path.isdir(config_dir):
            return metadata

        # VRChat stores cached world/avatar info in JSON files
        cache_path = os.path.join(config_dir, "cache.json")
        if os.path.exists(cache_path):
            try:
                data = self._load_json(cache_path)
                metadata["current_avatar"] = (
                    data.get("currentAvatar")
                    or data.get("avatar")
                    or data.get("lastAvatar")
                )
                metadata["current_world"] = (
                    data.get("currentWorld")
                    or data.get("world")
                    or data.get("lastWorld")
                )
                metadata["instance_id"] = (
                    data.get("instanceId")
                    or data.get("instance")
                )
            except (Exception,):
                pass

        return metadata

    def get_recording_hooks(self) -> list[Dict[str, Any]]:
        """Return recording hooks for VRChat sessions."""
        return [
            {
                "name": "vrchat_world_join",
                "event": "on_world_enter",
                "filter_fn": "capture_world_metadata",
                "description": "Record world name and instance on join",
            },
            {
                "name": "vrchat_avatar_change",
                "event": "on_avatar_change",
                "filter_fn": "track_avatar_switches",
                "description": "Tag recordings when avatar changes",
            },
            {
                "name": "vrchat_filter_loading",
                "event": "on_state_change",
                "filter_fn": "filter_world_loading",
                "description": "Skip recording during world loading screens",
            },
        ]

    def _resolve_config_path(self) -> Optional[str]:
        """Resolve the VRChat config directory for the current platform."""
        import sys

        platform = sys.platform
        if platform in self._DEFAULT_CONFIG_PATHS:
            path = self._DEFAULT_CONFIG_PATHS[platform]
            if os.path.isdir(path):
                return path
        # Fallback: try all known paths
        for path in self._DEFAULT_CONFIG_PATHS.values():
            if os.path.isdir(path):
                return path
        return None
