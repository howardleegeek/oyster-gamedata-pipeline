#!/usr/bin/env python3
"""Tests for rc17.4-form — launcher operator form.

Covers:
- Config path resolution (Windows / macOS / Linux / env override)
- Config save / load round-trip
- apply_config_to_env sets correct OYSTER_* env vars
- show_first_launch_form returns None when OYSTER_SKIP_FORM=1
- prompt_route_type returns last_used when OYSTER_SKIP_FORM=1
- _apply_operator_config integration (via oyster_play import)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Ensure bin/ is on sys.path so we can import the modules
BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from oyster_launcher_form import (  # noqa: E402
    CHARACTER_CLASSES,
    ROUTE_TYPES,
    apply_config_to_env,
    config_exists,
    load_config,
    prompt_route_type,
    save_config,
    show_first_launch_form,
    _config_path,
)

# All OYSTER_* env var keys used by the form
_OYSTER_KEYS = [
    "OYSTER_OPERATOR_ID",
    "OYSTER_CHARACTER_NAME",
    "OYSTER_CHARACTER_CLASS",
    "OYSTER_ROUTE_TYPE",
    "OYSTER_SCENE_NAME",
    "OYSTER_NOTES",
    "OYSTER_CONFIG_PATH",
    "OYSTER_SKIP_FORM",
]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_oyster_env():
    """Save and restore all OYSTER_* env vars around each test."""
    saved = {}
    for key in _OYSTER_KEYS:
        if key in os.environ:
            saved[key] = os.environ[key]
    # Clear them
    for key in _OYSTER_KEYS:
        os.environ.pop(key, None)
    yield
    # Restore
    for key, val in saved.items():
        os.environ[key] = val
    for key in _OYSTER_KEYS:
        if key not in saved:
            os.environ.pop(key, None)


@pytest.fixture
def tmp_config_path(tmp_path: Path):
    """Create a temp dir and set OYSTER_CONFIG_PATH to it."""
    cfg = tmp_path / "operator_config.json"
    os.environ["OYSTER_CONFIG_PATH"] = str(cfg)
    yield cfg


@pytest.fixture
def skip_form():
    """Set OYSTER_SKIP_FORM=1 for the duration of the test."""
    os.environ["OYSTER_SKIP_FORM"] = "1"
    yield


# --------------------------------------------------------------------------
# Config path tests
# --------------------------------------------------------------------------


class TestConfigPath:
    def test_env_override(self, tmp_path: Path):
        override = tmp_path / "custom" / "config.json"
        os.environ["OYSTER_CONFIG_PATH"] = str(override)
        assert _config_path() == override

    @pytest.mark.skipif(os.name != "nt", reason="Windows path test requires Windows")
    def test_windows_localappdata(self):
        with mock.patch.object(os, "name", "nt"):
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\test\\AppData\\Local"}):
                p = _config_path()
                assert "GameData Recorder" in str(p)
                assert p.name == "operator_config.json"

    def test_macos_xdg(self):
        with mock.patch.object(os, "name", "posix"):
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
                p = _config_path()
                assert str(p).startswith("/tmp/xdg/oyster/")


# --------------------------------------------------------------------------
# Config save / load tests
# --------------------------------------------------------------------------


class TestConfigSaveLoad:
    def test_save_and_load_roundtrip(self, tmp_config_path: Path):
        data = {
            "operator_id": "OP-001",
            "character_name": "Amiya",
            "character_class": "survival",
            "route_type": "2",
            "notes": "Test session",
        }
        assert save_config(data) is True
        assert tmp_config_path.is_file()

        loaded = load_config()
        assert loaded is not None
        assert loaded["operator_id"] == "OP-001"
        assert loaded["character_name"] == "Amiya"
        assert loaded["character_class"] == "survival"
        assert loaded["route_type"] == "2"
        assert loaded["notes"] == "Test session"

    def test_config_exists(self, tmp_config_path: Path):
        assert config_exists() is False
        save_config({"operator_id": "test"})
        assert config_exists() is True

    def test_load_nonexistent(self, tmp_config_path: Path):
        assert load_config() is None

    def test_load_corrupt_json(self, tmp_config_path: Path):
        tmp_config_path.write_text("{bad json", encoding="utf-8")
        assert load_config() is None


# --------------------------------------------------------------------------
# Env var application tests
# --------------------------------------------------------------------------


class TestApplyConfigToEnv:
    def test_sets_all_env_vars(self):
        cfg = {
            "operator_id": "OP-42",
            "character_name": "Ch'en",
            "character_class": "creative",
            "route_type": "3",
            "scene_name": "Lungmen",
            "notes": "Night ops",
        }
        apply_config_to_env(cfg)
        assert os.environ["OYSTER_OPERATOR_ID"] == "OP-42"
        assert os.environ["OYSTER_CHARACTER_NAME"] == "Ch'en"
        assert os.environ["OYSTER_CHARACTER_CLASS"] == "creative"
        assert os.environ["OYSTER_ROUTE_TYPE"] == "3"
        assert os.environ["OYSTER_SCENE_NAME"] == "Lungmen"
        assert os.environ["OYSTER_NOTES"] == "Night ops"

    def test_skips_empty_values(self):
        cfg = {"operator_id": "OP-1", "character_name": ""}
        apply_config_to_env(cfg)
        assert os.environ["OYSTER_OPERATOR_ID"] == "OP-1"
        assert "OYSTER_CHARACTER_NAME" not in os.environ

    def test_skips_missing_keys(self):
        cfg = {"operator_id": "OP-1"}
        apply_config_to_env(cfg)
        assert os.environ["OYSTER_OPERATOR_ID"] == "OP-1"
        assert "OYSTER_CHARACTER_CLASS" not in os.environ


# --------------------------------------------------------------------------
# Skip form tests
# --------------------------------------------------------------------------


class TestSkipForm:
    def test_first_launch_skipped(self, skip_form):
        result = show_first_launch_form()
        assert result is None

    def test_route_type_returns_last_used(self, skip_form):
        result = prompt_route_type(last_used="2")
        assert result == "2"

    def test_route_type_defaults_to_1(self, skip_form):
        result = prompt_route_type()
        assert result == "1"


# --------------------------------------------------------------------------
# Constants tests
# --------------------------------------------------------------------------


class TestConstants:
    def test_character_classes(self):
        assert CHARACTER_CLASSES == ["survival", "spectator", "creative"]

    def test_route_types(self):
        assert ROUTE_TYPES == {"1": "normal", "2": "special", "3": "loop"}


# --------------------------------------------------------------------------
# Integration: _apply_operator_config via oyster_play
# --------------------------------------------------------------------------


class TestApplyOperatorConfigIntegration:
    """Test the _apply_operator_config function from oyster_play.py."""

    def test_first_launch_skipped_no_config(self, tmp_config_path: Path, skip_form):
        """When config doesn't exist and form is skipped, nothing happens."""
        from oyster_play import _apply_operator_config

        _apply_operator_config()
        # No config was saved (form was skipped)
        assert not config_exists()

    def test_existing_config_sets_env(self, tmp_config_path: Path, skip_form):
        """When config exists, env vars are set."""
        from oyster_play import _apply_operator_config

        # Pre-create config
        save_config({
            "operator_id": "OP-99",
            "character_name": "Surtr",
            "character_class": "survival",
            "route_type": "1",
        })

        _apply_operator_config()

        assert os.environ["OYSTER_OPERATOR_ID"] == "OP-99"
        assert os.environ["OYSTER_CHARACTER_NAME"] == "Surtr"
        assert os.environ["OYSTER_CHARACTER_CLASS"] == "survival"
        # route_type is set by prompt_route_type which returns last_used when skipped
        assert os.environ["OYSTER_ROUTE_TYPE"] == "1"
