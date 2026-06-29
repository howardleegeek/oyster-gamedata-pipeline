#!/usr/bin/env python3
"""
bin/battery_aware_pause.py

Battery-aware pause utility for laptops. Detects battery vs AC power via
psutil and platform-specific methods (IOPSCopyPowerSourcesInfo on macOS via
pmset, sysfs on Linux). Pauses recording on battery (default) to preserve
laptop battery life. Supports configurable override per-game.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore
    HAS_PSUTIL = False

DEFAULT_CONFIG: Dict[str, Any] = {
    "pause_on_battery": True,
    "min_battery_percent": 20,
    "game_overrides": {},
}


def detect_power_source() -> Tuple[str, Optional[float], bool]:
    """Detect power source. Returns (source, battery_percent, is_plugged)."""
    if HAS_PSUTIL:
        try:
            b = psutil.sensors_battery()
            if b:
                return ("ac" if b.power_plugged else "battery", b.percent, bool(b.power_plugged))
        except (AttributeError, OSError):
            pass
    if sys.platform == "darwin":
        return _detect_macos()
    if sys.platform.startswith("linux"):
        return _detect_linux()
    return "unknown", None, False


def _detect_macos() -> Tuple[str, Optional[float], bool]:
    """Detect power on macOS using pmset (IOPSCopyPowerSourcesInfo)."""
    import subprocess

    try:
        r = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5)
        out = r.stdout
        if "AC Power" in out:
            m = re.search(r"(\d+)%", out)
            return "ac", float(m.group(1)) if m else None, True
        if "Battery Power" in out:
            m = re.search(r"(\d+)%", out)
            return "battery", float(m.group(1)) if m else None, False
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return "unknown", None, False


def _detect_linux() -> Tuple[str, Optional[float], bool]:
    """Detect power on Linux using sysfs."""
    p = "/sys/class/power_supply"
    if not os.path.exists(p):
        return "unknown", None, False
    try:
        for d in os.listdir(p):
            if not d.startswith("BAT"):
                continue
            with open(f"{p}/{d}/status") as f:
                status = f.read().strip().lower()
            pct = None
            try:
                with open(f"{p}/{d}/capacity") as f:
                    pct = float(f.read().strip())
            except (ValueError, OSError):
                pass
            if status in ("charging", "full"):
                return "ac", pct, True
            if status == "discharging":
                return "battery", pct, False
    except OSError:
        pass
    return "unknown", None, False


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from file, merging with defaults."""
    path = (
        Path(config_path)
        if config_path
        else Path.home() / ".config" / "battery_aware_pause" / "config.json"
    )
    config = DEFAULT_CONFIG.copy()
    if path.exists():
        try:
            with open(path) as f:
                config.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: Dict[str, Any], config_path: Optional[str] = None) -> None:
    """Save configuration to file atomically."""
    path = (
        Path(config_path)
        if config_path
        else Path.home() / ".config" / "battery_aware_pause" / "config.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, str(path))
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def should_pause(
    config: Dict[str, Any], game: Optional[str] = None, override: bool = False
) -> Tuple[bool, str]:
    """Determine if recording should pause. Returns (should_pause, reason)."""
    if override:
        return False, "Override flag set"
    src, pct, plugged = detect_power_source()
    if src == "ac":
        return False, f"On AC ({pct:.0f}%)" if pct else "On AC"
    if src == "unknown":
        return False, "Power state unknown"
    # On battery
    if game and game in config.get("game_overrides", {}) and not config["game_overrides"][game].get(
        "pause_on_battery", True
    ):
        return False, f"Game '{game}' override"
    if not config.get("pause_on_battery", True):
        return False, "Config: pause_on_battery=False"
    min_pct = config.get("min_battery_percent", 20)
    if pct is not None and pct < min_pct:
        return True, f"Battery critical ({pct:.0f}% < {min_pct}%)"
    return True, f"On battery ({pct:.0f}%)" if pct else "On battery"


def print_status(config: Dict[str, Any]) -> None:
    """Print current power status and configuration."""
    src, pct, plugged = detect_power_source()
    print("=== Battery-Aware Pause Status ===")
    print(f"Power Source: {src.upper()}")
    if pct is not None:
        print(f"Battery Level: {pct:.0f}%")
    print(f"Plugged In: {'Yes' if plugged else 'No'}")
    print(
        f"\nConfig: pause_on_battery={config.get('pause_on_battery', True)}, "
        f"min_battery_percent={config.get('min_battery_percent', 20)}"
    )


def main(argv: Optional[list] = None) -> int:
    """Main entry point. Returns 0=continue, 1=pause, 2=error."""
    parser = argparse.ArgumentParser(
        description="Battery-aware pause utility for laptop recording.",
        epilog="Exit: 0=continue, 1=pause, 2=error",
    )
    parser.add_argument("--game", metavar="NAME", help="Game identifier for override")
    parser.add_argument("--config", metavar="PATH", help="Config file path")
    parser.add_argument("--override", action="store_true", help="Force continue")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    parser.add_argument(
        "--pause-on-battery", choices=["true", "false"], help="Set default pause_on_battery"
    )
    parser.add_argument(
        "--set-game-override",
        nargs=2,
        metavar=("GAME", "BOOL"),
        help="Set per-game override (true/false)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)

    if args.pause_on_battery:
        config["pause_on_battery"] = args.pause_on_battery == "true"
        save_config(config, args.config)
        print(f"Set pause_on_battery = {config['pause_on_battery']}")

    if args.set_game_override:
        game, val = args.set_game_override
        config.setdefault("game_overrides", {})[game] = {"pause_on_battery": val == "true"}
        save_config(config, args.config)
        print(f"Set override for '{game}': {val}")

    if args.status:
        print_status(config)
        return 0

    pause, reason = should_pause(config, game=args.game, override=args.override)
    print(f"Power State: {reason}")
    print(f"Action: {'PAUSE' if pause else 'CONTINUE'}")
    return 1 if pause else 0


if __name__ == "__main__":
    sys.exit(main())
