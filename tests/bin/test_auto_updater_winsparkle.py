#!/usr/bin/env python3
"""
Tests for bin/auto_updater_winsparkle.py

Auto-update mechanism for desktop applications using WinSparkle / Squirrel.Mac
style update workflows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Import the module under test
from bin import auto_updater_winsparkle


class TestUpdateStatus:
    """Tests for UpdateStatus enum."""

    def test_enum_values(self) -> None:
        """Verify all expected enum values exist."""
        assert auto_updater_winsparkle.UpdateStatus.UP_TO_DATE.value == 1
        assert auto_updater_winsparkle.UpdateStatus.UPDATE_AVAILABLE.value == 2
        assert auto_updater_winsparkle.UpdateStatus.DOWNLOADING.value == 3
        assert auto_updater_winsparkle.UpdateStatus.DOWNLOADED.value == 4
        assert auto_updater_winsparkle.UpdateStatus.ERROR.value == 5


class TestUpdateInfo:
    """Tests for UpdateInfo dataclass."""

    def test_basic_creation(self) -> None:
        """Test UpdateInfo creation with required fields."""
        info = auto_updater_winsparkle.UpdateInfo(
            version="1.2.3",
            download_url="https://example.com/update.exe",
        )
        assert info.version == "1.2.3"
        assert info.download_url == "https://example.com/update.exe"
        assert info.file_size == 0
        assert info.checksum_sha256 == ""
        assert info.release_notes == ""
        assert info.critical is False
        assert info.release_date == ""

    def test_full_creation(self) -> None:
        """Test UpdateInfo creation with all fields."""
        info = auto_updater_winsparkle.UpdateInfo(
            version="2.0.0",
            download_url="https://example.com/v2.exe",
            file_size=1024000,
            checksum_sha256="abc123",
            release_notes="Bug fixes",
            critical=True,
            release_date="2024-01-15",
        )
        assert info.version == "2.0.0"
        assert info.file_size == 1024000
        assert info.checksum_sha256 == "abc123"
        assert info.release_notes == "Bug fixes"
        assert info.critical is True
        assert info.release_date == "2024-01-15"

    def test_to_dict(self) -> None:
        """Test UpdateInfo serialization to dict."""
        info = auto_updater_winsparkle.UpdateInfo(
            version="1.0.0",
            download_url="https://example.com/app.exe",
            file_size=500,
            checksum_sha256="deadbeef",
            release_notes="Release notes",
            critical=False,
            release_date="2024-01-01",
        )
        result = info.to_dict()
        assert isinstance(result, dict)
        assert result["version"] == "1.0.0"
        assert result["download_url"] == "https://example.com/app.exe"
        assert result["file_size"] == 500
        assert result["checksum_sha256"] == "deadbeef"
        assert result["release_notes"] == "Release notes"
        assert result["critical"] is False
        assert result["release_date"] == "2024-01-01"


class TestAppConfig:
    """Tests for AppConfig dataclass."""

    def test_basic_creation(self) -> None:
        """Test AppConfig creation with required fields."""
        config = auto_updater_winsparkle.AppConfig(
            app_name="MyApp",
            current_version="1.0.0",
            update_url="https://updates.example.com/feed.xml",
        )
        assert config.app_name == "MyApp"
        assert config.current_version == "1.0.0"
        assert config.update_url == "https://updates.example.com/feed.xml"
        assert config.poll_interval_hours == 24
        assert config.auto_install_critical is False

    def test_full_creation(self) -> None:
        """Test AppConfig creation with all fields."""
        download_dir = Path("/tmp/downloads")
        config = auto_updater_winsparkle.AppConfig(
            app_name="TestApp",
            current_version="1.5.0",
            update_url="https://updates.example.com/feed.json",
            poll_interval_hours=12,
            auto_install_critical=True,
            download_dir=download_dir,
        )
        assert config.app_name == "TestApp"
        assert config.current_version == "1.5.0"
        assert config.update_url == "https://updates.example.com/feed.json"
        assert config.poll_interval_hours == 12
        assert config.auto_install_critical is True
        assert config.download_dir == download_dir

    def test_default_download_dir(self) -> None:
        """Test that download_dir defaults to a temp directory."""
        config = auto_updater_winsparkle.AppConfig(
            app_name="App",
            current_version="1.0",
            update_url="https://example.com/feed.xml",
        )
        assert config.download_dir is not None
        assert isinstance(config.download_dir, Path)
        assert config.download_dir.name.startswith("updater_")


class TestUpdateState:
    """Tests for UpdateState dataclass."""

    def test_default_values(self) -> None:
        """Test UpdateState default initialization."""
        state = auto_updater_winsparkle.UpdateState()
        assert state.status == auto_updater_winsparkle.UpdateStatus.UP_TO_DATE
        assert state.last_check is None
        assert state.available_update is None
        assert state.downloaded_file is None
        assert state.error_message == ""
        assert state.download_progress == 0.0


class TestAutoUpdater:
    """Tests for AutoUpdater class."""

    @pytest.fixture
    def config(self, tmp_path: Path) -> auto_updater_winsparkle.AppConfig:
        """Create a test configuration."""
        return auto_updater_winsparkle.AppConfig(
            app_name="TestApp",
            current_version="1.0.0",
            update_url="https://updates.example.com/feed.xml",
            download_dir=tmp_path / "downloads",
        )

    @pytest.fixture
    def updater(self, config: auto_updater_winsparkle.AppConfig) -> auto_updater_winsparkle.AutoUpdater:
        """Create an AutoUpdater instance."""
        return auto_updater_winsparkle.AutoUpdater(config)

    def test_initialization(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test AutoUpdater initialization."""
        assert updater.config is not None
        assert updater.state is not None
        assert updater.state.status == auto_updater_winsparkle.UpdateStatus.UP_TO_DATE
        assert updater._stop_event is not None

    def test_is_newer_true(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test _is_newer returns True for newer versions."""
        assert updater._is_newer("1.1.0") is True
        assert updater._is_newer("2.0.0") is True
        assert updater._is_newer("1.0.1") is True

    def test_is_newer_false_same(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test _is_newer returns False for same version."""
        assert updater._is_newer("1.0.0") is False

    def test_is_newer_false_older(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test _is_newer returns False for older versions."""
        assert updater._is_newer("0.9.0") is False
        assert updater._is_newer("0.1.0") is False

    def test_is_newer_invalid(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test _is_newer handles invalid version strings."""
        assert updater._is_newer("invalid") is False
        assert updater._is_newer("") is False

    def test_parse_json_feed_valid(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test _parse_json_feed with valid JSON."""
        body = json.dumps({
            "version": "2.0.0",
            "url": "https://example.com/v2.exe",
            "size": 1024000,
            "sha256": "abc123def",
            "release_notes": "New features",
            "critical": True,
            "release_date": "2024-01-15",
        }).encode()
        result = updater._parse_json_feed(body)
        assert result is not None
        assert result.version == "2.0.0"
        assert result.download_url == "https://example.com/v2.exe"
        assert result.file_size == 1024000
        assert result.checksum_sha256 == "abc123def"
        assert result.critical is True

    def test_parse_json_feed_array(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test _parse_json_feed with array format."""
        body = json.dumps([{
            "version": "1.5.0",
            "url": "https://example.com/v1.5.exe",
            "size": 500000,
        }]).encode()
        result = updater._parse_json_feed(body)
        assert result is not None
        assert result.version == "1.5.0"

    def test_parse_json_feed_invalid(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test _parse_json_feed with invalid JSON."""
        body = b"not valid json"
        result = updater._parse_json_feed(body)
        assert result is None

    def test_parse_json_feed_older_version(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test _parse_json_feed ignores older versions."""
        body = json.dumps({
            "version": "0.5.0",
            "url": "https://example.com/v0.5.exe",
        }).encode()
        result = updater._parse_json_feed(body)
        assert result is None

    def test_verify_checksum_valid(self, updater: auto_updater_winsparkle.AutoUpdater, tmp_path: Path) -> None:
        """Test _verify_checksum with matching checksum."""
        test_file = tmp_path / "test.exe"
        test_file.write_bytes(b"test content")
        # Calculate SHA256 of "test content"
        import hashlib
        expected_hash = hashlib.sha256(b"test content").hexdigest()
        assert updater._verify_checksum(test_file, expected_hash) is True

    def test_verify_checksum_invalid(self, updater: auto_updater_winsparkle.AutoUpdater, tmp_path: Path) -> None:
        """Test _verify_checksum with mismatched checksum."""
        test_file = tmp_path / "test.exe"
        test_file.write_bytes(b"test content")
        assert updater._verify_checksum(test_file, "wronghash") is False

    def test_verify_checksum_empty(self, updater: auto_updater_winsparkle.AutoUpdater, tmp_path: Path) -> None:
        """Test _verify_checksum with empty checksum (skip verification)."""
        test_file = tmp_path / "test.exe"
        test_file.write_bytes(b"test content")
        assert updater._verify_checksum(test_file, "") is True

    def test_check_for_update_no_feed(self, updater: auto_updater_winsparkle.AutoUpdater) -> None:
        """Test check_for_update with unreachable URL."""
        updater.config.update_url = "https://this-domain-does-not-exist.invalid/feed.xml"
        result = updater.check_for_update()
        assert result is None
        assert updater.state.status == auto_updater_winsparkle.UpdateStatus.ERROR




class TestBuildParser:
    """Tests for CLI argument parser."""

    def test_parser_returns_argument_parser(self) -> None:
        """Test that _build_parser returns an ArgumentParser."""
        parser = auto_updater_winsparkle._build_parser()
        import argparse
        assert isinstance(parser, argparse.ArgumentParser)

    def test_parser_has_required_arguments(self) -> None:
        """Test parser has the required argument configuration."""
        parser = auto_updater_winsparkle._build_parser()
        # Check that required args are defined
        actions = {action.dest: action for action in parser._actions}
        assert "url" in actions
        assert "version" in actions
        assert actions["url"].required is True
        assert actions["version"].required is True

    def test_parser_has_optional_arguments(self) -> None:
        """Test parser has optional arguments with correct defaults."""
        parser = auto_updater_winsparkle._build_parser()
        actions = {action.dest: action for action in parser._actions}
        assert "app_name" in actions
        assert actions["app_name"].default == "MyApp"
        assert "poll_hours" in actions
        assert actions["poll_hours"].default == 24

    def test_parser_has_subparsers(self) -> None:
        """Test parser has subparsers configured."""
        parser = auto_updater_winsparkle._build_parser()
        # Find the subparser action
        subparser_action = None
        for action in parser._actions:
            if hasattr(action, "_parser_class"):
                subparser_action = action
                break
        assert subparser_action is not None


class TestMain:
    """Tests for main function."""

    def test_main_is_callable(self) -> None:
        """Test main function exists and is callable."""
        assert callable(auto_updater_winsparkle.main)

    def test_main_returns_int_on_missing_args(self) -> None:
        """Test main function returns exit code on missing args."""
        with pytest.raises(SystemExit):
            auto_updater_winsparkle.main([])


class TestConstants:
    """Tests for any module-level constants."""

    def test_module_docstring(self) -> None:
        """Verify module has proper documentation."""
        assert auto_updater_winsparkle.__doc__ is not None
        assert "auto_updater" in auto_updater_winsparkle.__doc__.lower() or "WinSparkle" in auto_updater_winsparkle.__doc__
