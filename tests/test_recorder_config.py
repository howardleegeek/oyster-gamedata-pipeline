#!/usr/bin/env python3
"""tests/test_recorder_config.py — Tests for bin/recorder_config.py."""

from __future__ import annotations

import json
import os

# We need to import from the project root
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bin.recorder_config import (
    _DEFAULT_CONFIG,
    REQUIRED_KEYS,
    ConfigError,
    _read_json,
    _validate,
    load,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for config files."""
    return tmp_path


@pytest.fixture
def valid_config(tmp_path: Path) -> Path:
    """Write a valid config file and return its path."""
    cfg = {
        "backend_url": "https://example.com/api",
        "discord_webhook": "https://discord.com/api/webhooks/123/abc",
        "update_server": "https://updates.example.com",
        "auto_update_check_hours": 12,
        "income_notification_time": "18:30",
        "telemetry_enabled": True,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    return path


@pytest.fixture
def minimal_config(tmp_path: Path) -> Path:
    """Write a minimal valid config (only required keys)."""
    cfg = {k: "" for k in REQUIRED_KEYS}
    cfg["auto_update_check_hours"] = 24
    cfg["telemetry_enabled"] = False
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    return path


# ---------------------------------------------------------------------------
# Tests: load() returns dict with required keys
# ---------------------------------------------------------------------------


class TestLoadReturnsRequiredKeys:
    """recorder_config.load() returns dict with required keys."""

    def test_load_with_valid_config(self, valid_config: Path) -> None:
        cfg = load(config_path=valid_config)
        assert isinstance(cfg, dict)
        for key in REQUIRED_KEYS:
            assert key in cfg, f"Missing required key: {key}"

    def test_load_values_match_file(self, valid_config: Path) -> None:
        cfg = load(config_path=valid_config)
        assert cfg["backend_url"] == "https://example.com/api"
        assert cfg["auto_update_check_hours"] == 12
        assert cfg["telemetry_enabled"] is True
        assert cfg["income_notification_time"] == "18:30"

    def test_load_with_minimal_config(self, minimal_config: Path) -> None:
        cfg = load(config_path=minimal_config)
        for key in REQUIRED_KEYS:
            assert key in cfg

    def test_load_returns_copy_not_reference(self, valid_config: Path) -> None:
        cfg1 = load(config_path=valid_config)
        cfg2 = load(config_path=valid_config)
        assert cfg1 is not cfg2


# ---------------------------------------------------------------------------
# Tests: missing config file → loads from default
# ---------------------------------------------------------------------------


class TestMissingConfigFallsBack:
    """Missing config file → loads from default."""

    def test_missing_file_uses_in_memory_default(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "does_not_exist.json"
        cfg = load(config_path=nonexistent)
        assert isinstance(cfg, dict)
        for key in REQUIRED_KEYS:
            assert key in cfg, f"Default config missing key: {key}"

    def test_default_config_has_expected_values(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nope.json"
        cfg = load(config_path=nonexistent)
        assert cfg["backend_url"] == "https://oyster-backend-stub.fly.dev"
        assert cfg["update_server"] == "https://updates.oyster.example"
        assert cfg["auto_update_check_hours"] == 24
        assert cfg["income_notification_time"] == "20:00"
        assert cfg["telemetry_enabled"] is False

    def test_installer_default_config_file(self) -> None:
        """installer/default_config.json exists and is valid JSON."""
        default_path = Path(__file__).resolve().parent.parent / "installer" / "default_config.json"
        assert default_path.exists(), "installer/default_config.json not found"
        data = json.loads(default_path.read_text())
        for key in REQUIRED_KEYS:
            assert key in data, f"Default config missing key: {key}"

    def test_fallback_to_installer_default(self, tmp_path: Path) -> None:
        """When user config is missing but installer/default_config.json exists,
        it should be used."""
        # Create a temp dir with no config
        nonexistent = tmp_path / "no_config.json"

        # The load() function will try:
        # 1. nonexistent (doesn't exist) → skip
        # 2. installer/default_config.json (exists) → use it
        cfg = load(config_path=nonexistent)
        assert cfg["backend_url"] == "https://oyster-backend-stub.fly.dev"


# ---------------------------------------------------------------------------
# Tests: env var override
# ---------------------------------------------------------------------------


class TestEnvVarOverride:
    """OYSTER_BACKEND_URL env var overrides backend_url."""

    def test_env_var_overrides_backend_url(self, valid_config: Path) -> None:
        with mock.patch.dict(os.environ, {"OYSTER_BACKEND_URL": "https://override.example"}):
            cfg = load(config_path=valid_config)
            assert cfg["backend_url"] == "https://override.example"

    def test_env_var_not_set_uses_config(self, valid_config: Path) -> None:
        # Ensure env var is not set
        env = dict(os.environ)
        env.pop("OYSTER_BACKEND_URL", None)
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = load(config_path=valid_config)
            assert cfg["backend_url"] == "https://example.com/api"

    def test_env_var_overrides_default(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nope.json"
        with mock.patch.dict(os.environ, {"OYSTER_BACKEND_URL": "https://env-override.dev"}):
            cfg = load(config_path=nonexistent)
            assert cfg["backend_url"] == "https://env-override.dev"

    def test_env_var_empty_string_no_override(self, valid_config: Path) -> None:
        # Empty string should NOT override (falsy check)
        with mock.patch.dict(os.environ, {"OYSTER_BACKEND_URL": ""}):
            cfg = load(config_path=valid_config)
            assert cfg["backend_url"] == "https://example.com/api"


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Config validation."""

    def test_missing_required_key_raises(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "bad.json"
        cfg_path.write_text(json.dumps({"backend_url": "https://x"}))
        with pytest.raises(ConfigError, match="Missing required config keys"):
            load(config_path=cfg_path)

    def test_multiple_missing_keys_raises(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "bad.json"
        cfg_path.write_text(json.dumps({"backend_url": "https://x", "discord_webhook": "y"}))
        with pytest.raises(ConfigError, match="Missing required config keys"):
            load(config_path=cfg_path)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "bad.json"
        cfg_path.write_text("not json {{{")
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load(config_path=cfg_path)

    def test_non_dict_json_raises(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "bad.json"
        cfg_path.write_text(json.dumps(["a", "b"]))
        with pytest.raises(ConfigError, match="must be a JSON object"):
            load(config_path=cfg_path)


# ---------------------------------------------------------------------------
# Tests: _read_json helper
# ---------------------------------------------------------------------------


class TestReadJson:
    """Internal _read_json helper."""

    def test_reads_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        p.write_text('{"key": "value"}')
        result = _read_json(p)
        assert result == {"key": "value"}

    def test_invalid_json_raises_config_error(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        p.write_text("{bad json")
        with pytest.raises(ConfigError):
            _read_json(p)


# ---------------------------------------------------------------------------
# Tests: _validate helper
# ---------------------------------------------------------------------------


class TestValidate:
    """Internal _validate helper."""

    def test_valid_config_passes(self) -> None:
        cfg = {k: "val" for k in REQUIRED_KEYS}
        _validate(cfg)  # should not raise

    def test_missing_key_raises(self) -> None:
        cfg = {"backend_url": "x"}
        with pytest.raises(ConfigError):
            _validate(cfg)

    def test_empty_dict_raises(self) -> None:
        with pytest.raises(ConfigError):
            _validate({})


# ---------------------------------------------------------------------------
# Tests: default config consistency
# ---------------------------------------------------------------------------


class TestDefaultConfigConsistency:
    """In-memory default and installer/default_config.json should match."""

    def test_in_memory_has_all_required_keys(self) -> None:
        for key in REQUIRED_KEYS:
            assert key in _DEFAULT_CONFIG

    def test_installer_matches_in_memory_keys(self) -> None:
        default_path = Path(__file__).resolve().parent.parent / "installer" / "default_config.json"
        data = json.loads(default_path.read_text())
        assert set(data.keys()) == set(_DEFAULT_CONFIG.keys())
