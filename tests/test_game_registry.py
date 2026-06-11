"""Tests for the game adapter registry.

Verifies:
  - 4 games are registered (mc, roblox, beamng, vrchat)
  - detect_running_game returns the correct adapter based on mock psutil
  - list_supported_games returns 4 names
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bin.games import (
    detect_running_game,
    get_adapter,
    list_supported_games,
    reset_registry,
)
from bin.games.base_adapter import BaseAdapter
from bin.games.registry import _discover_adapters

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the registry before and after each test for isolation."""
    reset_registry()
    yield
    reset_registry()


def _make_mock_process(name: str, exe: str):
    """Create a mock psutil process info dict."""
    proc = MagicMock()
    proc.info = {"name": name, "exe": exe}
    return proc


# ---------------------------------------------------------------------------
# Test: auto-discovery
# ---------------------------------------------------------------------------


class TestAutoDiscovery:
    """Tests for _discover_adapters()."""

    def test_discovers_four_adapters(self):
        adapters = _discover_adapters()
        assert len(adapters) == 4

    def test_all_adapters_subclass_base(self):
        adapters = _discover_adapters()
        for cls in adapters:
            assert issubclass(cls, BaseAdapter)

    def test_all_adapters_have_game_name(self):
        adapters = _discover_adapters()
        for cls in adapters:
            assert cls.GAME_NAME, f"{cls.__name__} has empty GAME_NAME"

    def test_no_duplicate_game_names(self):
        adapters = _discover_adapters()
        names = [cls.GAME_NAME for cls in adapters]
        assert len(names) == len(set(names)), "Duplicate GAME_NAME found"

    def test_sorted_by_game_name(self):
        adapters = _discover_adapters()
        names = [cls.GAME_NAME for cls in adapters]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# Test: list_supported_games
# ---------------------------------------------------------------------------


class TestListSupportedGames:
    """Tests for list_supported_games()."""

    def test_returns_four_games(self):
        games = list_supported_games()
        assert len(games) == 4

    def test_contains_expected_games(self):
        games = set(list_supported_games())
        expected = {"mc", "roblox", "beamng", "vrchat"}
        assert games == expected

    def test_returns_sorted_list(self):
        games = list_supported_games()
        assert games == sorted(games)

    def test_returns_strings(self):
        games = list_supported_games()
        for g in games:
            assert isinstance(g, str)


# ---------------------------------------------------------------------------
# Test: detect_running_game
# ---------------------------------------------------------------------------


class TestDetectRunningGame:
    """Tests for detect_running_game() with mock psutil."""

    def test_detects_beamng(self):
        mock_proc = _make_mock_process(
            "BeamNG.drive.exe",
            "C:\\Program Files\\BeamNG.drive\\BeamNG.drive.exe",
        )
        adapter = detect_running_game(psutil_process_iter=lambda attrs: [mock_proc])
        assert adapter is not None
        assert adapter.GAME_NAME == "beamng"

    def test_detects_roblox(self):
        mock_proc = _make_mock_process(
            "RobloxPlayerBeta.exe",
            "C:\\Program Files\\Roblox\\Versions\\RobloxPlayerBeta.exe",
        )
        adapter = detect_running_game(psutil_process_iter=lambda attrs: [mock_proc])
        assert adapter is not None
        assert adapter.GAME_NAME == "roblox"

    def test_detects_minecraft(self):
        mock_proc = _make_mock_process(
            "javaw.exe",
            "C:\\Program Files\\Minecraft\\runtime\\jre-x64\\bin\\javaw.exe",
        )
        adapter = detect_running_game(psutil_process_iter=lambda attrs: [mock_proc])
        assert adapter is not None
        assert adapter.GAME_NAME == "mc"

    def test_detects_vrchat(self):
        mock_proc = _make_mock_process(
            "VRChat.exe",
            "C:\\Program Files\\Steam\\steamapps\\common\\VRChat\\VRChat.exe",
        )
        adapter = detect_running_game(psutil_process_iter=lambda attrs: [mock_proc])
        assert adapter is not None
        assert adapter.GAME_NAME == "vrchat"

    def test_returns_none_when_no_game_running(self):
        mock_proc = _make_mock_process(
            "chrome.exe",
            "C:\\Program Files\\Google\\Chrome\\chrome.exe",
        )
        adapter = detect_running_game(psutil_process_iter=lambda attrs: [mock_proc])
        assert adapter is None

    def test_returns_none_when_no_processes(self):
        adapter = detect_running_game(psutil_process_iter=lambda attrs: [])
        assert adapter is None

    def test_returns_first_match(self):
        """When multiple game processes are running, returns the first match."""
        beamng_proc = _make_mock_process(
            "BeamNG.drive.exe",
            "C:\\BeamNG\\BeamNG.drive.exe",
        )
        roblox_proc = _make_mock_process(
            "RobloxPlayerBeta.exe",
            "C:\\Roblox\\RobloxPlayerBeta.exe",
        )
        # Registry is sorted by GAME_NAME: beamng, mc, roblox, vrchat
        # beamng should be checked first and match
        adapter = detect_running_game(psutil_process_iter=lambda attrs: [beamng_proc, roblox_proc])
        assert adapter is not None
        assert adapter.GAME_NAME == "beamng"

    def test_returns_instance_not_class(self):
        mock_proc = _make_mock_process(
            "VRChat.exe",
            "C:\\VRChat\\VRChat.exe",
        )
        adapter = detect_running_game(psutil_process_iter=lambda attrs: [mock_proc])
        assert adapter is not None
        assert isinstance(adapter, BaseAdapter)
        # Should be an instance, not a class
        assert not isinstance(adapter, type)


# ---------------------------------------------------------------------------
# Test: get_adapter
# ---------------------------------------------------------------------------


class TestGetAdapter:
    """Tests for get_adapter()."""

    def test_get_beamng(self):
        cls = get_adapter("beamng")
        assert cls is not None
        assert cls.GAME_NAME == "beamng"

    def test_get_mc(self):
        cls = get_adapter("mc")
        assert cls is not None
        assert cls.GAME_NAME == "mc"

    def test_get_roblox(self):
        cls = get_adapter("roblox")
        assert cls is not None
        assert cls.GAME_NAME == "roblox"

    def test_get_vrchat(self):
        cls = get_adapter("vrchat")
        assert cls is not None
        assert cls.GAME_NAME == "vrchat"

    def test_get_unknown_returns_none(self):
        cls = get_adapter("nonexistent_game")
        assert cls is None

    def test_case_sensitive(self):
        cls = get_adapter("BeamNG")
        assert cls is None


# ---------------------------------------------------------------------------
# Test: reset_registry
# ---------------------------------------------------------------------------


class TestResetRegistry:
    """Tests for reset_registry()."""

    def test_reset_clears_cache(self):
        # Populate the cache
        list_supported_games()
        # Reset
        reset_registry()
        # Cache should be cleared — next call re-discovers
        games = list_supported_games()
        assert len(games) == 4
