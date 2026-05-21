"""Tests for BeamNG.drive game adapter."""

import json
from unittest.mock import MagicMock, patch

import pytest

from bin.games import detect_running_game
from bin.games.base_adapter import BaseAdapter
from bin.games.beamng_adapter import BeamNGAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_beamng_process_win():
    """Mock Windows BeamNG process."""
    proc = MagicMock()
    proc.info = {
        "name": "BeamNG.drive.exe",
        "exe": "C:\\Program Files\\BeamNG.drive\\BeamNG.drive.exe",
    }
    return proc


@pytest.fixture
def mock_beamng_process_linux():
    """Mock Linux BeamNG process."""
    proc = MagicMock()
    proc.info = {
        "name": "BeamNG.drive",
        "exe": "/home/user/.local/share/Steam/steamapps/common/BeamNG.drive/BeamNG.drive",
    }
    return proc


@pytest.fixture
def mock_non_beamng_process():
    """Mock a non-BeamNG process."""
    proc = MagicMock()
    proc.info = {
        "name": "chrome.exe",
        "exe": "C:\\Program Files\\Google\\Chrome\\chrome.exe",
    }
    return proc


@pytest.fixture
def sample_settings_dict():
    """Sample BeamNG settings.json content."""
    return {
        "vehicle": {"name": "etk800", "id": "etk800"},
        "map": {"name": "west_coast_usa", "id": "west_coast_usa"},
        "gameMode": "freeroam",
        "graphics": {"quality": "high"},
    }


@pytest.fixture
def sample_settings_flat():
    """Sample settings with flat (non-dict) values."""
    return {
        "lastVehicle": "civbolide",
        "lastMap": "gridmap_v2",
        "game_mode": "time_trial",
    }


@pytest.fixture
def sample_settings_gameplay_nested():
    """Sample settings with gameplay-nested values."""
    return {
        "gameplay": {
            "vehicle": "sunburst",
            "map": "italy",
            "mode": "career",
        },
    }


# ---------------------------------------------------------------------------
# Test: detect()
# ---------------------------------------------------------------------------


class TestBeamNGDetect:
    """Tests for BeamNGAdapter.detect()."""

    def test_detect_windows_exe(self):
        assert BeamNGAdapter.detect("BeamNG.drive.exe", "C:\\BeamNG\\BeamNG.drive.exe")

    def test_detect_linux_binary(self):
        assert BeamNGAdapter.detect("BeamNG.drive", "/home/user/BeamNG.drive")

    def test_detect_case_insensitive(self):
        assert BeamNGAdapter.detect("beamng.drive.exe", "c:\\beamng\\beamng.drive.exe")

    def test_detect_by_exe_path_only(self):
        # Process name might differ but exe path contains identifier
        assert BeamNGAdapter.detect(
            "some_launcher.exe", "C:\\Games\\BeamNG.drive\\BeamNG.drive.exe"
        )

    def test_detect_non_beamng(self):
        assert not BeamNGAdapter.detect("chrome.exe", "C:\\Chrome\\chrome.exe")

    def test_detect_empty_strings(self):
        assert not BeamNGAdapter.detect("", "")

    def test_detect_partial_match_in_name(self):
        # Only exact match or exe path match should work
        assert not BeamNGAdapter.detect("beamng", "C:\\other\\game.exe")


# ---------------------------------------------------------------------------
# Test: extract_metadata()
# ---------------------------------------------------------------------------


class TestBeamNGExtractMetadata:
    """Tests for BeamNGAdapter.extract_metadata()."""

    def test_metadata_returns_game_name(self, tmp_path, sample_settings_dict):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(sample_settings_dict))

        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata(str(settings_file))

        assert meta["game_name"] == "beamng"

    def test_metadata_vehicle_dict(self, tmp_path, sample_settings_dict):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(sample_settings_dict))

        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata(str(settings_file))

        assert meta["vehicle"] == "etk800"

    def test_metadata_map_dict(self, tmp_path, sample_settings_dict):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(sample_settings_dict))

        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata(str(settings_file))

        assert meta["map"] == "west_coast_usa"

    def test_metadata_game_mode(self, tmp_path, sample_settings_dict):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(sample_settings_dict))

        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata(str(settings_file))

        assert meta["game_mode"] == "freeroam"

    def test_metadata_flat_values(self, tmp_path, sample_settings_flat):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(sample_settings_flat))

        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata(str(settings_file))

        assert meta["vehicle"] == "civbolide"
        assert meta["map"] == "gridmap_v2"
        assert meta["game_mode"] == "time_trial"

    def test_metadata_nested_gameplay(self, tmp_path, sample_settings_gameplay_nested):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(sample_settings_gameplay_nested))

        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata(str(settings_file))

        assert meta["vehicle"] == "sunburst"
        assert meta["map"] == "italy"
        assert meta["game_mode"] == "career"

    def test_metadata_missing_file(self):
        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata("/nonexistent/path/settings.json")

        assert meta["game_name"] == "beamng"
        assert meta["vehicle"] is None
        assert meta["map"] is None
        assert meta["game_mode"] is None

    def test_metadata_invalid_json(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{invalid json}")

        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata(str(settings_file))

        assert meta["game_name"] == "beamng"
        assert meta["vehicle"] is None

    def test_metadata_empty_settings(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}")

        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata(str(settings_file))

        assert meta["game_name"] == "beamng"
        assert meta["vehicle"] is None
        assert meta["map"] is None
        assert meta["game_mode"] is None

    def test_metadata_none_settings_path(self):
        adapter = BeamNGAdapter()
        meta = adapter.extract_metadata(None)

        assert meta["game_name"] == "beamng"


# ---------------------------------------------------------------------------
# Test: get_recording_hooks()
# ---------------------------------------------------------------------------


class TestBeamNGRecordingHooks:
    """Tests for BeamNGAdapter.get_recording_hooks()."""

    def test_hooks_return_list(self):
        adapter = BeamNGAdapter()
        hooks = adapter.get_recording_hooks()

        assert isinstance(hooks, list)
        assert len(hooks) > 0

    def test_hooks_have_required_keys(self):
        adapter = BeamNGAdapter()
        hooks = adapter.get_recording_hooks()

        for hook in hooks:
            assert "name" in hook
            assert "event" in hook
            assert "filter_fn" in hook

    def test_hooks_prefer_driving_missions(self):
        adapter = BeamNGAdapter()
        hooks = adapter.get_recording_hooks()

        hook_names = [h["name"] for h in hooks]
        assert "beamng_driving_mission" in hook_names

    def test_hooks_filter_menu(self):
        adapter = BeamNGAdapter()
        hooks = adapter.get_recording_hooks()

        hook_names = [h["name"] for h in hooks]
        assert "beamng_filter_menu" in hook_names

    def test_hooks_filter_out_menu_time(self):
        adapter = BeamNGAdapter()
        hooks = adapter.get_recording_hooks()

        menu_hook = next(h for h in hooks if h["name"] == "beamng_filter_menu")
        assert menu_hook["filter_fn"] == "filter_out_menu_time"


# ---------------------------------------------------------------------------
# Test: detect_running_game()
# ---------------------------------------------------------------------------


class TestDetectRunningGame:
    """Tests for the detect_running_game() function."""

    def test_detect_beamng_windows(self, mock_beamng_process_win):
        mock_iter = MagicMock(return_value=[mock_beamng_process_win])

        adapter = detect_running_game(psutil_process_iter=mock_iter)

        assert adapter is not None
        assert isinstance(adapter, BeamNGAdapter)
        assert adapter.GAME_NAME == "beamng"

    def test_detect_beamng_linux(self, mock_beamng_process_linux):
        mock_iter = MagicMock(return_value=[mock_beamng_process_linux])

        adapter = detect_running_game(psutil_process_iter=mock_iter)

        assert adapter is not None
        assert isinstance(adapter, BeamNGAdapter)

    def test_detect_no_game(self, mock_non_beamng_process):
        mock_iter = MagicMock(return_value=[mock_non_beamng_process])

        adapter = detect_running_game(psutil_process_iter=mock_iter)

        assert adapter is None

    def test_detect_empty_process_list(self):
        mock_iter = MagicMock(return_value=[])

        adapter = detect_running_game(psutil_process_iter=mock_iter)

        assert adapter is None

    def test_detect_beamng_among_other_processes(
        self, mock_beamng_process_win, mock_non_beamng_process
    ):
        mock_iter = MagicMock(return_value=[mock_non_beamng_process, mock_beamng_process_win])

        adapter = detect_running_game(psutil_process_iter=mock_iter)

        assert adapter is not None
        assert isinstance(adapter, BeamNGAdapter)

    def test_detect_handles_no_such_process(self, mock_beamng_process_win, mock_non_beamng_process):
        import psutil

        def failing_iter(*_args, **_kwargs):
            yield mock_non_beamng_process
            raise psutil.NoSuchProcess(pid=1234)

        mock_iter = MagicMock(side_effect=failing_iter)

        # Should not raise, should return None or adapter
        try:
            detect_running_game(psutil_process_iter=mock_iter)
        except psutil.NoSuchProcess:
            pytest.fail("detect_running_game should handle NoSuchProcess gracefully")

    def test_detect_handles_access_denied(self):
        import psutil

        def failing_iter(*_args, **_kwargs):
            raise psutil.AccessDenied(pid=5678)

        mock_iter = MagicMock(side_effect=failing_iter)

        try:
            detect_running_game(psutil_process_iter=mock_iter)
        except psutil.AccessDenied:
            pytest.fail("detect_running_game should handle AccessDenied gracefully")


# ---------------------------------------------------------------------------
# Test: BaseAdapter inheritance
# ---------------------------------------------------------------------------


class TestBeamNGInheritance:
    """Tests that BeamNGAdapter properly inherits from BaseAdapter."""

    def test_is_subclass(self):
        assert issubclass(BeamNGAdapter, BaseAdapter)

    def test_instance_of_base(self):
        adapter = BeamNGAdapter()
        assert isinstance(adapter, BaseAdapter)

    def test_game_name_set(self):
        adapter = BeamNGAdapter()
        assert adapter.GAME_NAME == "beamng"

    def test_repr(self):
        adapter = BeamNGAdapter()
        assert "BeamNGAdapter" in repr(adapter)
        assert "beamng" in repr(adapter)


# ---------------------------------------------------------------------------
# Test: _resolve_settings_path()
# ---------------------------------------------------------------------------


class TestResolveSettingsPath:
    """Tests for BeamNGAdapter._resolve_settings_path()."""

    @patch("sys.platform", "win32")
    def test_resolve_windows_path(self):
        adapter = BeamNGAdapter()
        path = adapter._resolve_settings_path()
        assert path is not None
        assert "BeamNG.drive" in path
        assert "settings.json" in path

    @patch("sys.platform", "linux")
    def test_resolve_linux_path(self):
        adapter = BeamNGAdapter()
        path = adapter._resolve_settings_path()
        assert path is not None
        assert "BeamNG.drive" in path
        assert "settings.json" in path
