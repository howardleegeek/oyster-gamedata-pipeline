"""Tests for the Roblox game adapter.

Coverage matrix:

* ``detect()`` returns ``None`` when Roblox is not running.
* ``detect()`` returns a ``GameSession`` when a mock Roblox process is
  found (both Windows and macOS exe names).
* ``extract_metadata()`` returns a ``GameMetadata`` with
  ``game_name='roblox'`` and parsed place_id / universe_id from mock
  log files.
* ``pre_record_hook`` / ``post_record_hook`` execute without error.
* ``detect_running_game()`` from the registry returns the adapter when
  Roblox is running, ``None`` otherwise.
* All psutil / os.path interactions are mocked — no real Roblox client
  is required.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil

from bin.games import detect_running_game, get_adapter
from bin.games.base_adapter import GameMetadata, GameSession
from bin.games.roblox_adapter import (
    OVERLAY_MARKER,
    RobloxAdapter,
    _extract_ids_from_logs,
    _find_roblox_process,
    _roblox_exe_name,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_proc(
    pid: int = 42,
    name: str = "RobloxPlayerBeta.exe",
    exe: str = "/fake/RobloxPlayerBeta.exe",
) -> MagicMock:
    """Create a mock psutil.Process with the given attributes."""
    proc = MagicMock()
    proc.pid = pid
    proc.info = {"pid": pid, "name": name, "exe": exe}
    proc.exe.return_value = exe
    proc.name.return_value = name
    return proc


# ---------------------------------------------------------------------------
# _roblox_exe_name
# ---------------------------------------------------------------------------


class TestRobloExeName:
    def test_windows_exe(self):
        with patch.object(platform, "system", return_value="Windows"):
            assert _roblox_exe_name() == "RobloxPlayerBeta.exe"

    def test_mac_exe(self):
        with patch.object(platform, "system", return_value="Darwin"):
            assert _roblox_exe_name() == "RobloxPlayer.app"

    def test_linux_falls_back_to_mac_name(self):
        # Linux isn't a primary target; the code defaults to the macOS name
        with patch.object(platform, "system", return_value="Linux"):
            assert _roblox_exe_name() == "RobloxPlayer.app"


# ---------------------------------------------------------------------------
# _find_roblox_process
# ---------------------------------------------------------------------------


class TestFindRobloxProcess:
    def test_no_roblox_running(self):
        """When no process matches, returns None."""
        with patch("bin.games.roblox_adapter.psutil.process_iter", return_value=[]):
            assert _find_roblox_process() is None

    def test_roblox_found_by_name(self):
        """Process matched by name field."""
        mock_proc = _make_mock_proc()
        with (
            patch("bin.games.roblox_adapter.platform.system", return_value="Windows"),
            patch("bin.games.roblox_adapter.psutil.process_iter", return_value=[mock_proc]),
        ):
            result = _find_roblox_process()
            assert result is mock_proc

    def test_roblox_found_by_exe_basename(self):
        """Process matched by exe path basename even if name differs."""
        mock_proc = _make_mock_proc(name="something_else", exe="/some/path/RobloxPlayerBeta.exe")
        with (
            patch("bin.games.roblox_adapter.platform.system", return_value="Windows"),
            patch("bin.games.roblox_adapter.psutil.process_iter", return_value=[mock_proc]),
        ):
            result = _find_roblox_process()
            assert result is mock_proc

    def test_mac_roblox_found(self):
        """macOS process detection."""
        mock_proc = _make_mock_proc(name="RobloxPlayer.app", exe="/Applications/RobloxPlayer.app")
        with (
            patch("bin.games.roblox_adapter.platform.system", return_value="Darwin"),
            patch("bin.games.roblox_adapter.psutil.process_iter", return_value=[mock_proc]),
        ):
            result = _find_roblox_process()
            assert result is mock_proc

    def test_access_denied_skipped(self):
        """psutil.AccessDenied on a process is silently skipped."""
        bad_proc = MagicMock()
        bad_proc.info = {"pid": 1, "name": None, "exe": None}
        with (
            patch("bin.games.roblox_adapter.platform.system", return_value="Windows"),
            patch("bin.games.roblox_adapter.psutil.process_iter", return_value=[bad_proc]),
        ):
            # The loop should handle the None gracefully and return None
            result = _find_roblox_process()
            assert result is None

    def test_process_iter_raises(self):
        """If process_iter itself raises, we return None (no crash)."""
        with (
            patch("bin.games.roblox_adapter.platform.system", return_value="Windows"),
            patch("bin.games.roblox_adapter.psutil.process_iter", side_effect=OSError("boom")),
        ):
            result = _find_roblox_process()
            assert result is None


# ---------------------------------------------------------------------------
# RobloxAdapter.detect()
# ---------------------------------------------------------------------------


class TestRobloxAdapterDetect:
    def test_detect_returns_none_when_not_running(self):
        adapter = RobloxAdapter()
        with patch("bin.games.roblox_adapter._find_roblox_process", return_value=None):
            assert adapter.detect() is None

    def test_detect_returns_session_when_running(self):
        mock_proc = _make_mock_proc(pid=1234)
        adapter = RobloxAdapter()
        with patch("bin.games.roblox_adapter._find_roblox_process", return_value=mock_proc):
            session = adapter.detect()
            assert session is not None
            assert isinstance(session, GameSession)
            assert session.pid == 1234
            assert session.exe_path == "/fake/RobloxPlayerBeta.exe"

    def test_detect_handles_exe_access_denied(self):
        """If proc.exe() raises AccessDenied, detect returns None."""
        mock_proc = _make_mock_proc()
        mock_proc.exe.side_effect = psutil.AccessDenied(pid=1)
        adapter = RobloxAdapter()
        with patch("bin.games.roblox_adapter._find_roblox_process", return_value=mock_proc):
            assert adapter.detect() is None

    def test_detect_handles_name_access_denied(self):
        """If proc.name() raises, we still get a session with fallback title."""
        mock_proc = _make_mock_proc()
        mock_proc.name.side_effect = psutil.AccessDenied(pid=1)
        adapter = RobloxAdapter()
        with patch("bin.games.roblox_adapter._find_roblox_process", return_value=mock_proc):
            session = adapter.detect()
            assert session is not None
            assert session.window_title == "Roblox"


# ---------------------------------------------------------------------------
# _extract_ids_from_logs
# ---------------------------------------------------------------------------


class TestExtractIdsFromLogs:
    def test_nonexistent_dir(self, tmp_path: Path):
        fake_dir = tmp_path / "no_such_dir"
        result = _extract_ids_from_logs(fake_dir)
        assert result == {"place_id": "", "universe_id": ""}

    def test_empty_dir(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = _extract_ids_from_logs(log_dir)
        assert result == {"place_id": "", "universe_id": ""}

    def test_place_id_extracted(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "Player.log").write_text("some noise\nplace_id=12345678\nmore noise\n")
        result = _extract_ids_from_logs(log_dir)
        assert result["place_id"] == "12345678"
        assert result["universe_id"] == ""

    def test_universe_id_extracted(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "Player.log").write_text("universe_id: 987654321\n")
        result = _extract_ids_from_logs(log_dir)
        assert result["place_id"] == ""
        assert result["universe_id"] == "987654321"

    def test_both_ids_extracted(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "Player.log").write_text("place_id=11111\nuniverse_id=22222\n")
        result = _extract_ids_from_logs(log_dir)
        assert result["place_id"] == "11111"
        assert result["universe_id"] == "22222"

    def test_ids_across_multiple_files(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "old.log").write_text("place_id=100\n")
        (log_dir / "new.log").write_text("universe_id=200\n")
        # Make new.log more recent
        os.utime(log_dir / "new.log", (999999, 999999))
        os.utime(log_dir / "old.log", (100000, 100000))
        result = _extract_ids_from_logs(log_dir)
        assert result["place_id"] == "100"
        assert result["universe_id"] == "200"

    def test_alternative_regex_formats(self, tmp_path: Path):
        """Test various log formats Roblox might use."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "Player.log").write_text("place_id: 55555\nuniverse_id = 66666\n")
        result = _extract_ids_from_logs(log_dir)
        assert result["place_id"] == "55555"
        assert result["universe_id"] == "66666"


# ---------------------------------------------------------------------------
# RobloxAdapter.extract_metadata()
# ---------------------------------------------------------------------------


class TestRobloxAdapterExtractMetadata:
    def test_metadata_has_game_name_roblox(self, tmp_path: Path):
        adapter = RobloxAdapter()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "Player.log").write_text("place_id=999\nuniverse_id=888\n")
        with patch("bin.games.roblox_adapter._roblox_log_dir", return_value=log_dir):
            meta = adapter.extract_metadata(pid=1)
        assert isinstance(meta, GameMetadata)
        assert meta.game_name == "roblox"
        assert meta.place_id == "999"
        assert meta.universe_id == "888"

    def test_metadata_empty_when_no_logs(self, tmp_path: Path):
        adapter = RobloxAdapter()
        fake_dir = tmp_path / "nonexistent"
        with patch("bin.games.roblox_adapter._roblox_log_dir", return_value=fake_dir):
            meta = adapter.extract_metadata(pid=1)
        assert meta.game_name == "roblox"
        assert meta.place_id == ""
        assert meta.universe_id == ""


# ---------------------------------------------------------------------------
# RobloxAdapter hooks
# ---------------------------------------------------------------------------


class TestRobloxAdapterHooks:
    def test_pre_record_hook_no_error(self):
        adapter = RobloxAdapter()
        session = GameSession(pid=1, window_title="Roblox", exe_path="/fake")
        # Should not raise
        adapter.pre_record_hook(session)

    def test_post_record_hook_no_error(self):
        adapter = RobloxAdapter()
        session = GameSession(pid=1, window_title="Roblox", exe_path="/fake")
        adapter.post_record_hook(session)

    def test_overlay_marker_constant(self):
        assert OVERLAY_MARKER == "Recording for Oyster"


# ---------------------------------------------------------------------------
# Registry: detect_running_game / get_adapter
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_detect_running_game_returns_none_when_no_game(self):
        with patch("bin.games.roblox_adapter._find_roblox_process", return_value=None):
            result = detect_running_game()
            assert result is None

    def test_detect_running_game_returns_adapter_when_roblox_running(self):
        mock_proc = _make_mock_proc()
        with patch("bin.games.roblox_adapter._find_roblox_process", return_value=mock_proc):
            result = detect_running_game()
            assert result is not None
            assert result.game_name == "roblox"
            assert isinstance(result, RobloxAdapter)

    def test_get_adapter_by_name(self):
        adapter = get_adapter("roblox")
        assert adapter is not None
        assert adapter.game_name == "roblox"

    def test_get_adapter_unknown_name(self):
        assert get_adapter("minecraft") is None
        assert get_adapter("nonexistent") is None


# ---------------------------------------------------------------------------
# Import smoke test
# ---------------------------------------------------------------------------


class TestImportSmoke:
    def test_from_bin_games_import_detect_running_game(self):
        from bin.games import detect_running_game as drg

        assert callable(drg)

    def test_from_bin_games_import_roblox_adapter(self):
        from bin.games import RobloxAdapter as RA

        assert RA is not None

    def test_from_bin_games_import_base_classes(self):
        from bin.games import GameAdapter, GameMetadata, GameSession

        assert GameAdapter is not None
        assert GameSession is not None
        assert GameMetadata is not None
