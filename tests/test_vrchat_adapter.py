"""Tests for the VRChat game adapter.

Coverage matrix:

* ``VRChatAdapter`` inherits from ``GameAdapter`` (and provides
  ``BaseAdapter``-compatible methods).
* ``detect()`` returns ``None`` when VRChat is not running.
* ``detect()`` returns a ``GameSession`` when a mock VRChat process is
  found (both Windows and macOS exe names).
* ``extract_metadata()`` returns a ``GameMetadata`` with
  ``game_name='vrchat'`` and parsed world_id / instance_id from mock
  log files.
* ``pre_record_hook`` blocks recording for private worlds/instances.
* ``pre_record_hook`` allows recording for public worlds.
* ``post_record_hook`` executes without error.
* ``get_recording_hooks()`` returns the privacy filter hook.
* ``detect_by_process()`` (BaseAdapter-compatible) works with mock psutil.
* ``_is_private_world()`` correctly identifies private instances.
* ``detect_running_game()`` from the registry returns the adapter when
  VRChat is running.
* All psutil / os.path interactions are mocked — no real VRChat client
  is required.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil

from bin.games import detect_running_game, get_adapter
from bin.games.base_adapter import GameAdapter, GameMetadata, GameSession
from bin.games.vrchat_adapter import (
    VRChatAdapter,
    _extract_world_id_from_logs,
    _find_vrchat_process,
    _is_private_world,
    _vrchat_exe_name,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_proc(
    pid: int = 42,
    name: str = "VRChat.exe",
    exe: str = "/fake/VRChat.exe",
) -> MagicMock:
    """Create a mock psutil.Process with the given attributes."""
    proc = MagicMock()
    proc.pid = pid
    proc.info = {"pid": pid, "name": name, "exe": exe}
    proc.exe.return_value = exe
    proc.name.return_value = name
    return proc


# ---------------------------------------------------------------------------
# Inheritance check
# ---------------------------------------------------------------------------


class TestVRChatAdapterInheritance:
    """Verify VRChatAdapter inherits from GameAdapter."""

    def test_inherits_game_adapter(self):
        adapter = VRChatAdapter()
        assert isinstance(adapter, GameAdapter)

    def test_game_name_property(self):
        adapter = VRChatAdapter()
        assert adapter.game_name == "vrchat"

    def test_class_constant(self):
        assert VRChatAdapter.GAME_NAME == "vrchat"


# ---------------------------------------------------------------------------
# _vrchat_exe_name
# ---------------------------------------------------------------------------


class TestVrchatExeName:
    def test_windows_exe(self):
        with patch.object(platform, "system", return_value="Windows"):
            assert _vrchat_exe_name() == "VRChat.exe"

    def test_mac_exe(self):
        with patch.object(platform, "system", return_value="Darwin"):
            assert _vrchat_exe_name() == "vrchat.app"

    def test_linux_falls_back(self):
        with patch.object(platform, "system", return_value="Linux"):
            assert _vrchat_exe_name() == "vrchat.app"


# ---------------------------------------------------------------------------
# _find_vrchat_process
# ---------------------------------------------------------------------------


class TestFindVrchatProcess:
    def test_no_vrchat_running(self):
        """When no process matches, returns None."""
        with patch("bin.games.vrchat_adapter.psutil.process_iter", return_value=[]):
            assert _find_vrchat_process() is None

    def test_vrchat_found_by_name_windows(self):
        """Process matched by name field on Windows."""
        mock_proc = _make_mock_proc()
        with (
            patch("bin.games.vrchat_adapter.platform.system", return_value="Windows"),
            patch("bin.games.vrchat_adapter.psutil.process_iter", return_value=[mock_proc]),
        ):
            result = _find_vrchat_process()
            assert result is mock_proc

    def test_vrchat_found_by_exe_basename(self):
        """Process matched by exe path basename even if name differs."""
        mock_proc = _make_mock_proc(name="something_else", exe="C:\\Games\\VRChat\\VRChat.exe")
        with (
            patch("bin.games.vrchat_adapter.platform.system", return_value="Windows"),
            patch("bin.games.vrchat_adapter.psutil.process_iter", return_value=[mock_proc]),
        ):
            result = _find_vrchat_process()
            assert result is mock_proc

    def test_mac_vrchat_found(self):
        """macOS process detection."""
        mock_proc = _make_mock_proc(name="vrchat.app", exe="/Applications/vrchat.app")
        with (
            patch("bin.games.vrchat_adapter.platform.system", return_value="Darwin"),
            patch("bin.games.vrchat_adapter.psutil.process_iter", return_value=[mock_proc]),
        ):
            result = _find_vrchat_process()
            assert result is mock_proc

    def test_access_denied_skipped(self):
        """psutil.AccessDenied on a process is silently skipped."""
        bad_proc = MagicMock()
        bad_proc.info = {"pid": 1, "name": None, "exe": None}
        with (
            patch("bin.games.vrchat_adapter.platform.system", return_value="Windows"),
            patch("bin.games.vrchat_adapter.psutil.process_iter", return_value=[bad_proc]),
        ):
            result = _find_vrchat_process()
            assert result is None

    def test_process_iter_raises(self):
        """If process_iter itself raises, we return None (no crash)."""
        with (
            patch("bin.games.vrchat_adapter.platform.system", return_value="Windows"),
            patch("bin.games.vrchat_adapter.psutil.process_iter", side_effect=OSError("boom")),
        ):
            result = _find_vrchat_process()
            assert result is None


# ---------------------------------------------------------------------------
# VRChatAdapter.detect()
# ---------------------------------------------------------------------------


class TestVRChatAdapterDetect:
    def test_detect_returns_none_when_not_running(self):
        adapter = VRChatAdapter()
        with patch("bin.games.vrchat_adapter._find_vrchat_process", return_value=None):
            result = adapter.detect()
            assert result is None

    def test_detect_returns_session_when_running(self):
        adapter = VRChatAdapter()
        mock_proc = _make_mock_proc(pid=1234)
        with patch("bin.games.vrchat_adapter._find_vrchat_process", return_value=mock_proc):
            result = adapter.detect()
            assert result is not None
            assert isinstance(result, GameSession)
            assert result.pid == 1234
            assert result.window_title == "VRChat.exe"
            assert result.exe_path == "/fake/VRChat.exe"

    def test_detect_exe_access_denied(self):
        adapter = VRChatAdapter()
        mock_proc = _make_mock_proc()
        mock_proc.exe.side_effect = psutil.AccessDenied(pid=1)
        with patch("bin.games.vrchat_adapter._find_vrchat_process", return_value=mock_proc):
            result = adapter.detect()
            assert result is None


# ---------------------------------------------------------------------------
# _extract_world_id_from_logs
# ---------------------------------------------------------------------------


class TestExtractWorldIdFromLogs:
    def test_nonexistent_dir(self, tmp_path: Path):
        fake_dir = tmp_path / "no_such_dir"
        result = _extract_world_id_from_logs(fake_dir)
        assert result == {"world_id": "", "instance_id": ""}

    def test_empty_dir(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = _extract_world_id_from_logs(log_dir)
        assert result == {"world_id": "", "instance_id": ""}

    def test_world_id_extracted(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "output_log_2024.01.01_120000.txt").write_text(
            "some noise\nOnJoinedWorld: wrld_a1b2c3d4-e5f6-7890-abcd-ef1234567890\nmore noise\n"
        )
        result = _extract_world_id_from_logs(log_dir)
        assert result["world_id"] == "wrld_a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert result["instance_id"] == ""

    def test_world_id_with_equals_format(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "output_log_2024.01.01_120000.txt").write_text("world_id=wrld_abc123\n")
        result = _extract_world_id_from_logs(log_dir)
        assert result["world_id"] == "wrld_abc123"

    def test_instance_id_extracted(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "output_log_2024.01.01_120000.txt").write_text(
            "instance_id=12345~hidden(usr_xxx)\n"
        )
        result = _extract_world_id_from_logs(log_dir)
        assert result["world_id"] == ""
        assert result["instance_id"] == "12345~hidden(usr_xxx)"

    def test_both_ids_extracted(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "output_log_2024.01.01_120000.txt").write_text(
            "OnJoinedWorld: wrld_xyz789\ninstance_id=99999~friends\n"
        )
        result = _extract_world_id_from_logs(log_dir)
        assert result["world_id"] == "wrld_xyz789"
        assert result["instance_id"] == "99999~friends"

    def test_ids_across_multiple_files(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "output_log_old.txt").write_text("world_id=wrld_old\n")
        (log_dir / "output_log_new.txt").write_text("instance_id=111~public\n")
        # Make new.log more recent
        os.utime(log_dir / "output_log_new.txt", (999999, 999999))
        os.utime(log_dir / "output_log_old.txt", (100000, 100000))
        result = _extract_world_id_from_logs(log_dir)
        assert result["world_id"] == "wrld_old"
        assert result["instance_id"] == "111~public"


# ---------------------------------------------------------------------------
# _is_private_world
# ---------------------------------------------------------------------------


class TestIsPrivateWorld:
    def test_public_world(self):
        assert not _is_private_world("wrld_public123", "12345~public")

    def test_hidden_instance(self):
        assert _is_private_world("wrld_abc", "12345~hidden(usr_xxx)")

    def test_friends_instance(self):
        assert _is_private_world("wrld_abc", "12345~friends")

    def test_invite_instance(self):
        assert _is_private_world("wrld_abc", "12345~invite(usr_yyy)")

    def test_private_instance(self):
        assert _is_private_world("wrld_abc", "12345~private")

    def test_private_world_prefix(self):
        assert _is_private_world("wrld_private_something", "12345~public")

    def test_empty_ids_not_private(self):
        assert not _is_private_world("", "")

    def test_case_insensitive(self):
        assert _is_private_world("wrld_abc", "12345~HIDDEN(usr_xxx)")


# ---------------------------------------------------------------------------
# VRChatAdapter.extract_metadata()
# ---------------------------------------------------------------------------


class TestVRChatAdapterExtractMetadata:
    def test_metadata_has_game_name_vrchat(self, tmp_path: Path):
        adapter = VRChatAdapter()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "output_log_2024.01.01_120000.txt").write_text(
            "OnJoinedWorld: wrld_999\ninstance_id=888~public\n"
        )
        with patch("bin.games.vrchat_adapter._vrchat_log_dir", return_value=log_dir):
            meta = adapter.extract_metadata(pid=1)
        assert isinstance(meta, GameMetadata)
        assert meta.game_name == "vrchat"
        assert meta.world_id == "wrld_999"
        assert meta.instance_id == "888~public"

    def test_metadata_empty_when_no_logs(self, tmp_path: Path):
        adapter = VRChatAdapter()
        fake_dir = tmp_path / "nonexistent"
        with patch("bin.games.vrchat_adapter._vrchat_log_dir", return_value=fake_dir):
            meta = adapter.extract_metadata(pid=1)
        assert meta.game_name == "vrchat"
        assert meta.world_id == ""
        assert meta.instance_id == ""


# ---------------------------------------------------------------------------
# VRChatAdapter hooks
# ---------------------------------------------------------------------------


class TestVRChatAdapterHooks:
    def test_pre_record_hook_allows_public_world(self):
        adapter = VRChatAdapter()
        session = GameSession(pid=1, window_title="VRChat", exe_path="/fake")
        with patch.object(
            adapter,
            "extract_metadata",
            return_value=GameMetadata(
                game_name="vrchat",
                world_id="wrld_public123",
                instance_id="12345~public",
            ),
        ):
            result = adapter.pre_record_hook(session)
        assert result is True

    def test_pre_record_hook_blocks_hidden_instance(self):
        adapter = VRChatAdapter()
        session = GameSession(pid=1, window_title="VRChat", exe_path="/fake")
        with patch.object(
            adapter,
            "extract_metadata",
            return_value=GameMetadata(
                game_name="vrchat",
                world_id="wrld_abc",
                instance_id="12345~hidden(usr_xxx)",
            ),
        ):
            result = adapter.pre_record_hook(session)
        assert result is False

    def test_pre_record_hook_blocks_friends_instance(self):
        adapter = VRChatAdapter()
        session = GameSession(pid=1, window_title="VRChat", exe_path="/fake")
        with patch.object(
            adapter,
            "extract_metadata",
            return_value=GameMetadata(
                game_name="vrchat",
                world_id="wrld_abc",
                instance_id="12345~friends",
            ),
        ):
            result = adapter.pre_record_hook(session)
        assert result is False

    def test_pre_record_hook_blocks_invite_instance(self):
        adapter = VRChatAdapter()
        session = GameSession(pid=1, window_title="VRChat", exe_path="/fake")
        with patch.object(
            adapter,
            "extract_metadata",
            return_value=GameMetadata(
                game_name="vrchat",
                world_id="wrld_abc",
                instance_id="12345~invite(usr_yyy)",
            ),
        ):
            result = adapter.pre_record_hook(session)
        assert result is False

    def test_pre_record_hook_blocks_private_world_prefix(self):
        adapter = VRChatAdapter()
        session = GameSession(pid=1, window_title="VRChat", exe_path="/fake")
        with patch.object(
            adapter,
            "extract_metadata",
            return_value=GameMetadata(
                game_name="vrchat",
                world_id="wrld_private_something",
                instance_id="12345~public",
            ),
        ):
            result = adapter.pre_record_hook(session)
        assert result is False

    def test_post_record_hook_no_error(self):
        adapter = VRChatAdapter()
        session = GameSession(pid=1, window_title="VRChat", exe_path="/fake")
        adapter.post_record_hook(session)  # should not raise


# ---------------------------------------------------------------------------
# VRChatAdapter.get_recording_hooks()
# ---------------------------------------------------------------------------


class TestVRChatAdapterRecordingHooks:
    def test_hooks_returned(self):
        adapter = VRChatAdapter()
        hooks = adapter.get_recording_hooks()
        assert isinstance(hooks, list)
        assert len(hooks) == 3

    def test_private_world_filter_hook(self):
        adapter = VRChatAdapter()
        hooks = adapter.get_recording_hooks()
        filter_hook = hooks[0]
        assert filter_hook["name"] == "vrchat_private_world_filter"
        assert filter_hook["event"] == "on_world_join"
        assert filter_hook["filter_fn"] == "skip_private_worlds"

    def test_world_metadata_hook(self):
        adapter = VRChatAdapter()
        hooks = adapter.get_recording_hooks()
        meta_hook = hooks[1]
        assert meta_hook["name"] == "vrchat_world_metadata"
        assert meta_hook["event"] == "on_world_change"

    def test_instance_type_hook(self):
        adapter = VRChatAdapter()
        hooks = adapter.get_recording_hooks()
        inst_hook = hooks[2]
        assert inst_hook["name"] == "vrchat_instance_type"
        assert inst_hook["event"] == "on_instance_join"


# ---------------------------------------------------------------------------
# BaseAdapter-compatible methods
# ---------------------------------------------------------------------------


class TestBaseAdapterCompatibleMethods:
    def test_detect_by_process_windows(self):
        assert VRChatAdapter.detect_by_process("VRChat.exe", "C:\\VRChat\\VRChat.exe")

    def test_detect_by_process_mac(self):
        assert VRChatAdapter.detect_by_process("vrchat.app", "/Applications/vrchat.app")

    def test_detect_by_process_case_insensitive(self):
        assert VRChatAdapter.detect_by_process("vrchat.exe", "c:\\vrchat\\vrchat.exe")

    def test_detect_by_process_exe_path_only(self):
        assert VRChatAdapter.detect_by_process("some_launcher.exe", "C:\\Games\\VRChat\\VRChat.exe")

    def test_detect_by_process_non_vrchat(self):
        assert not VRChatAdapter.detect_by_process("chrome.exe", "C:\\Chrome\\chrome.exe")

    def test_extract_metadata_legacy_with_path(self, tmp_path: Path):
        adapter = VRChatAdapter()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "output_log_2024.01.01_120000.txt").write_text(
            "OnJoinedWorld: wrld_legacy_test\n"
        )
        metadata = adapter.extract_metadata_legacy(settings_path=str(log_dir))
        assert metadata["game_name"] == "vrchat"
        assert metadata["world_id"] == "wrld_legacy_test"
        assert metadata["instance_id"] is None

    def test_extract_metadata_legacy_no_path(self, tmp_path: Path):
        adapter = VRChatAdapter()
        fake_dir = tmp_path / "nonexistent"
        with patch("bin.games.vrchat_adapter._vrchat_log_dir", return_value=fake_dir):
            metadata = adapter.extract_metadata_legacy()
        assert metadata["game_name"] == "vrchat"
        assert metadata["world_id"] is None
        assert metadata["instance_id"] is None


# ---------------------------------------------------------------------------
# Registry: detect_running_game / get_adapter
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_detect_running_game_returns_none_when_no_game(self):
        with patch("bin.games.vrchat_adapter._find_vrchat_process", return_value=None):
            result = detect_running_game()
            assert result is None

    def test_detect_running_game_returns_adapter_when_vrchat_running(self):
        mock_proc = _make_mock_proc()
        with patch("bin.games.vrchat_adapter._find_vrchat_process", return_value=mock_proc):
            result = detect_running_game()
            assert result is not None
            assert result.game_name == "vrchat"
            assert isinstance(result, VRChatAdapter)

    def test_get_adapter_by_name(self):
        adapter = get_adapter("vrchat")
        assert adapter is not None
        assert adapter.game_name == "vrchat"

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

    def test_from_bin_games_import_vrchat_adapter(self):
        from bin.games import VRChatAdapter as VA

        assert VA is not None

    def test_from_bin_games_import_base_classes(self):
        from bin.games import GameAdapter, GameMetadata, GameSession

        assert GameAdapter is not None
        assert GameSession is not None
        assert GameMetadata is not None
