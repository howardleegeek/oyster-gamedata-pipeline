#!/usr/bin/env python3
"""recorder_config.py — Central config loader for OysterRecorder.

Reads ``~/.oyster/config.json`` and provides a ``load()`` function that
returns a dict with all required keys.  Supports:

* **Default fallback** — if the user config file is missing, a built-in
  default config is used (shipped as ``installer/default_config.json``).
* **Environment-variable override** — ``OYSTER_BACKEND_URL`` overrides
  the ``backend_url`` key.
* **Validation** — required keys are checked; missing keys raise
  ``ConfigError``.

Required keys in the returned dict:
    - backend_url
    - discord_webhook
    - update_server
    - auto_update_check_hours
    - income_notification_time
    - telemetry_enabled

Usage
-----
    from bin.recorder_config import load

    cfg = load()
    print(cfg["backend_url"])
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OYSTER_DIR = Path.home() / ".oyster"
CONFIG_PATH = OYSTER_DIR / "config.json"

# Default config is shipped alongside the installer; fall back to an
# in-memory dict if the file is not found (e.g. during development).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "installer" / "default_config.json"

REQUIRED_KEYS = [
    "backend_url",
    "discord_webhook",
    "update_server",
    "auto_update_check_hours",
    "income_notification_time",
    "telemetry_enabled",
]

# In-memory default — mirrors installer/default_config.json
_DEFAULT_CONFIG: Dict[str, Any] = {
    "backend_url": "https://oyster-backend-6qup7rrx2q-uc.a.run.app",
    "discord_webhook": "",
    "update_server": "https://updates.oyster.example",
    "auto_update_check_hours": 24,
    "income_notification_time": "20:00",
    "telemetry_enabled": False,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised when the config is invalid or cannot be loaded."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the OysterRecorder configuration.

    Resolution order:
        1. ``config_path`` argument (if provided)
        2. ``~/.oyster/config.json``
        3. ``installer/default_config.json`` (relative to project root)
        4. In-memory default dict

    After loading, ``OYSTER_BACKEND_URL`` env var (if set) overrides the
    ``backend_url`` key.

    Parameters
    ----------
    config_path : Path, optional
        Explicit path to a config JSON file.

    Returns
    -------
    dict
        Configuration dict with all required keys.

    Raises
    ------
    ConfigError
        If a config file exists but is invalid JSON or missing required keys.
    """
    path = config_path or CONFIG_PATH
    config = _load_from_path(path)

    # Environment variable override
    env_url = os.environ.get("OYSTER_BACKEND_URL")
    if env_url:
        config["backend_url"] = env_url

    _validate(config)
    return config


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_from_path(path: Path) -> Dict[str, Any]:
    """Try to load config from *path*, falling back through defaults."""
    # 1. Try the given path
    if path.exists():
        return _read_json(path)

    # 2. Try the installer default config file
    if DEFAULT_CONFIG_PATH.exists():
        return _read_json(DEFAULT_CONFIG_PATH)

    # 3. Fall back to in-memory default
    return dict(_DEFAULT_CONFIG)


def _read_json(path: Path) -> Dict[str, Any]:
    """Read and parse a JSON file, raising ConfigError on failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Config in {path} must be a JSON object")

    return data


def _validate(config: Dict[str, Any]) -> None:
    """Ensure all required keys are present."""
    missing = [k for k in REQUIRED_KEYS if k not in config]
    if missing:
        raise ConfigError(f"Missing required config keys: {', '.join(missing)}")
