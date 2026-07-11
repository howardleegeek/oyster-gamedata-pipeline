#!/usr/bin/env python3
"""
bin/recorder_mc_config_reader.py — Minecraft client config reader (G270, W31).

Purpose
-------
Parse the local Minecraft Java Edition installation for the values the
recorder currently hardcodes (FOV=70, character_name='DataPilot',
screen_resolution='1920x1080', gamma=1.0). Replaces guesses with the
tester's *actual* configuration so generated `gameinfo.xlsx` and
`action_camera.json` intrinsics line up with what the camera sees.

Two files are inspected, both inside `%APPDATA%\\.minecraft` on Windows
(or `~/Library/Application Support/minecraft` on macOS, `~/.minecraft`
on Linux):

* `options.txt` — line-oriented `key:value` pairs. Keys of interest:
  `fov`, `gamma`, `renderDistance`, `guiScale`, `fullscreenResolution`,
  `overrideHeight`, `overrideWidth`. FOV is stored on a -1.0..+1.0 scale
  where -1.0 = 30°, 0.0 = 70°, +1.0 = 110° (vanilla quirk) — we map it
  back to degrees so downstream consumers don't have to.
* `launcher_profiles.json` — JSON. We pull `selectedUser.account` and
  cross-reference `authenticationDatabase[<account>].profiles` for the
  most recently used `displayName`.

Returned dict keys (stable contract — recorder + gameinfo writers depend
on these names):

    {
        "character_name": str,           # MC username (or "DataPilot" fallback)
        "fov_degrees": float,            # 30..110
        "gamma": float,                  # 0..1
        "screen_resolution": "WxH",      # "1920x1080" fallback
        "render_distance": int,          # chunks, 2..32
        "gui_scale": int,                # 0=auto..4
        "config_path": str,              # absolute path of options.txt found
        "username_source": str,          # "launcher_profiles" | "fallback"
        "warnings": list[str],           # human-readable issues
    }

Standalone — no third-party imports. Safe to run on systems without
Minecraft installed (returns sensible fallbacks + warning list).

Usage
-----
    >>> from recorder_mc_config_reader import read_mc_config
    >>> cfg = read_mc_config()
    >>> cfg["fov_degrees"]
    70.0
    >>> cfg["character_name"]
    'Steve'

CLI:
    python recorder_mc_config_reader.py             # human readable
    python recorder_mc_config_reader.py --json      # machine readable
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CHARACTER_NAME = "DataPilot"
DEFAULT_FOV_DEGREES = 70.0
DEFAULT_GAMMA = 1.0
DEFAULT_RESOLUTION = "1920x1080"
DEFAULT_RENDER_DISTANCE = 12
DEFAULT_GUI_SCALE = 0


def _candidate_minecraft_dirs() -> List[Path]:
    """Return platform-appropriate ordered candidates for the .minecraft folder."""
    candidates: List[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / ".minecraft")
    home = Path.home()
    candidates.extend(
        [
            home / "Library" / "Application Support" / "minecraft",
            home / ".minecraft",
            home / "AppData" / "Roaming" / ".minecraft",
        ]
    )
    seen: List[Path] = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
    return seen


def _find_minecraft_dir(override: Optional[Path] = None) -> Optional[Path]:
    """Pick the first existing candidate (or honour ``override`` if given)."""
    if override is not None:
        p = Path(override)
        return p if p.is_dir() else None
    for cand in _candidate_minecraft_dirs():
        if cand.is_dir():
            return cand
    return None


def _vanilla_fov_to_degrees(raw: float) -> float:
    """Vanilla MC stores FOV on -1..+1 mapping to 30..110 degrees."""
    raw = max(-1.0, min(1.0, raw))
    return 70.0 + raw * 40.0


def _parse_options_txt(path: Path, warnings: List[str]) -> Dict[str, Any]:
    """Pull the keys we care about. Tolerant: any malformed line is skipped."""
    parsed: Dict[str, Any] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warnings.append(f"options.txt unreadable: {exc}")
        return parsed
    for _line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        parsed[key.strip()] = value.strip()
    out: Dict[str, Any] = {}
    if "fov" in parsed:
        try:
            out["fov_degrees"] = round(_vanilla_fov_to_degrees(float(parsed["fov"])), 2)
        except ValueError:
            warnings.append(f"fov value not float: {parsed['fov']!r}")
    if "gamma" in parsed:
        try:
            out["gamma"] = round(float(parsed["gamma"]), 3)
        except ValueError:
            warnings.append(f"gamma value not float: {parsed['gamma']!r}")
    if "renderDistance" in parsed:
        try:
            out["render_distance"] = int(parsed["renderDistance"])
        except ValueError:
            warnings.append(f"renderDistance not int: {parsed['renderDistance']!r}")
    if "guiScale" in parsed:
        try:
            out["gui_scale"] = int(parsed["guiScale"])
        except ValueError:
            warnings.append(f"guiScale not int: {parsed['guiScale']!r}")
    width = parsed.get("overrideWidth")
    height = parsed.get("overrideHeight")
    if width and height and width.isdigit() and height.isdigit() and int(width) > 0 and int(height) > 0:
        out["screen_resolution"] = f"{int(width)}x{int(height)}"
    elif "fullscreenResolution" in parsed and "x" in parsed["fullscreenResolution"]:
        out["screen_resolution"] = parsed["fullscreenResolution"].split("@", 1)[0]
    return out


def _parse_launcher_profiles(path: Path, warnings: List[str]) -> Optional[str]:
    """Best-effort current-user lookup. Modern MC uses MSA accounts."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"launcher_profiles.json unreadable: {exc}")
        return None
    selected = data.get("selectedUser") or {}
    account_id = selected.get("account")
    auth_db = data.get("authenticationDatabase") or {}
    if account_id and account_id in auth_db:
        profiles = auth_db[account_id].get("profiles") or {}
        for prof in profiles.values():
            name = prof.get("displayName")
            if isinstance(name, str) and name.strip():
                return name.strip()
    for entry in auth_db.values():
        for prof in (entry.get("profiles") or {}).values():
            name = prof.get("displayName")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def read_mc_config(minecraft_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Public entry point. Always returns a complete dict with safe fallbacks."""
    warnings: List[str] = []
    mc_dir = _find_minecraft_dir(minecraft_dir)
    if mc_dir is None:
        warnings.append(".minecraft directory not found — using defaults")
        return {
            "character_name": DEFAULT_CHARACTER_NAME,
            "fov_degrees": DEFAULT_FOV_DEGREES,
            "gamma": DEFAULT_GAMMA,
            "screen_resolution": DEFAULT_RESOLUTION,
            "render_distance": DEFAULT_RENDER_DISTANCE,
            "gui_scale": DEFAULT_GUI_SCALE,
            "config_path": "",
            "username_source": "fallback",
            "warnings": warnings,
        }
    options_path = mc_dir / "options.txt"
    parsed = _parse_options_txt(options_path, warnings) if options_path.is_file() else {}
    if not options_path.is_file():
        warnings.append(f"options.txt missing at {options_path}")
    profiles_path = mc_dir / "launcher_profiles.json"
    username = _parse_launcher_profiles(profiles_path, warnings) if profiles_path.is_file() else None
    return {
        "character_name": username or DEFAULT_CHARACTER_NAME,
        "fov_degrees": parsed.get("fov_degrees", DEFAULT_FOV_DEGREES),
        "gamma": parsed.get("gamma", DEFAULT_GAMMA),
        "screen_resolution": parsed.get("screen_resolution", DEFAULT_RESOLUTION),
        "render_distance": parsed.get("render_distance", DEFAULT_RENDER_DISTANCE),
        "gui_scale": parsed.get("gui_scale", DEFAULT_GUI_SCALE),
        "config_path": str(options_path),
        "username_source": "launcher_profiles" if username else "fallback",
        "warnings": warnings,
    }


def _main(argv: List[str]) -> int:
    as_json = "--json" in argv
    cfg = read_mc_config()
    if as_json:
        sys.stdout.write(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
        return 0
    print(f"Minecraft config (source: {cfg['config_path'] or '<defaults>'}):")
    for key in (
        "character_name",
        "fov_degrees",
        "gamma",
        "screen_resolution",
        "render_distance",
        "gui_scale",
        "username_source",
    ):
        print(f"  {key:18s} = {cfg[key]}")
    if cfg["warnings"]:
        print("Warnings:")
        for w in cfg["warnings"]:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
