"""Minecraft game adapter.

Detects a running Minecraft (Java Edition) process by process name
and extracts world/server metadata from the game's options.txt and
server list.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from bin.games.base_adapter import BaseAdapter


class MinecraftAdapter(BaseAdapter):
    """Adapter for Minecraft (Java Edition)."""

    GAME_NAME = "mc"

    # Process names to detect (Windows, macOS, Linux)
    _PROCESS_NAMES = ("javaw.exe", "java", "minecraft", "Minecraft")

    # Default .minecraft paths per platform
    _DEFAULT_MC_PATHS = {
        "win32": os.path.join(os.path.expanduser("~"), "AppData", "Roaming", ".minecraft"),
        "darwin": os.path.join(os.path.expanduser("~"), "Library", "Application Support", "minecraft"),
        "linux": os.path.join(os.path.expanduser("~"), ".minecraft"),
    }

    @classmethod
    def detect(cls, process_name: str, process_exe: str) -> bool:
        """Detect if a process belongs to Minecraft.

        Checks for java/javaw processes with minecraft in the command line
        or executable path.
        """
        name_lower = process_name.lower()
        exe_lower = process_exe.lower()

        # Direct process name match
        for target in cls._PROCESS_NAMES:
            if name_lower == target.lower():
                return True

        # Check if exe path contains minecraft
        if "minecraft" in exe_lower:
            return True

        # java/javaw with minecraft in path
        if name_lower in ("javaw.exe", "java"):
            if "minecraft" in exe_lower or ".minecraft" in exe_lower:
                return True

        return False

    def extract_metadata(self, settings_path: Optional[str] = None) -> Dict[str, Any]:
        """Extract metadata from Minecraft's options.txt and related files.

        Args:
            settings_path: Optional override path to .minecraft directory.

        Returns:
            Dict with game_name, last_server, render_distance, fov keys.
        """
        metadata: Dict[str, Any] = {
            "game_name": self.GAME_NAME,
            "last_server": None,
            "render_distance": None,
            "fov": None,
        }

        mc_dir = settings_path or self._resolve_mc_path()
        if mc_dir is None or not os.path.isdir(mc_dir):
            return metadata

        options_path = os.path.join(mc_dir, "options.txt")
        if not os.path.exists(options_path):
            return metadata

        try:
            with open(options_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()

                    if key == "renderDistance":
                        try:
                            metadata["render_distance"] = int(value)
                        except ValueError:
                            pass
                    elif key == "fov":
                        try:
                            metadata["fov"] = float(value)
                        except ValueError:
                            pass
        except OSError:
            pass

        # Check servers.dat for last server
        servers_path = os.path.join(mc_dir, "servers.dat")
        if os.path.exists(servers_path):
            try:
                with open(servers_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    # Simple heuristic: look for IP-like patterns
                    import re
                    ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', content)
                    if ip_match:
                        metadata["last_server"] = ip_match.group(1)
            except OSError:
                pass

        return metadata

    def get_recording_hooks(self) -> list[Dict[str, Any]]:
        """Return recording hooks for Minecraft gameplay."""
        return [
            {
                "name": "mc_world_load",
                "event": "on_world_load",
                "filter_fn": "track_world_changes",
                "description": "Tag recordings when entering a new world",
            },
            {
                "name": "mc_server_join",
                "event": "on_server_connect",
                "filter_fn": "capture_server_info",
                "description": "Record server address and player count on join",
            },
            {
                "name": "mc_filter_loading",
                "event": "on_state_change",
                "filter_fn": "filter_loading_screens",
                "description": "Skip recording during terrain loading",
            },
        ]

    def _resolve_mc_path(self) -> Optional[str]:
        """Resolve the .minecraft directory path for the current platform."""
        import sys

        platform = sys.platform
        if platform in self._DEFAULT_MC_PATHS:
            path = self._DEFAULT_MC_PATHS[platform]
            if os.path.isdir(path):
                return path
        # Fallback: try all known paths
        for path in self._DEFAULT_MC_PATHS.values():
            if os.path.isdir(path):
                return path
        return None
