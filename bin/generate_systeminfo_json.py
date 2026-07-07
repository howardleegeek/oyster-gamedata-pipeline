#!/usr/bin/env python3
"""
Generate systeminfo.json file with Minecraft window information.
"""

import argparse
import json
import logging
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)


def detect_screen_dpi() -> float:
    """Auto-detect via subprocess on macOS / linux. Default 1.0."""
    try:
        if sys.platform == "darwin":  # macOS
            # Try to get display scaling factor
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleDisplayScaleFactor"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                dpi = float(result.stdout.strip())
                if dpi > 0:
                    return dpi
        elif sys.platform.startswith("linux"):
            # Try xrandr to get scaling
            result = subprocess.run(
                ["xrandr", "--query"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                # Look for scaling factor in output
                for line in result.stdout.splitlines():
                    if "scale factor" in line.lower():
                        # Try to parse "scale factor: 1.5" format
                        parts = line.split(":")
                        if len(parts) >= 2:
                            try:
                                dpi = float(parts[1].strip())
                                if dpi > 0:
                                    return dpi
                            except ValueError as e:
                                logger.debug("generate_systeminfo: xrandr scale-factor parse failed on line %r: %s", line, e)
    except (subprocess.SubprocessError, FileNotFoundError, ValueError) as e:
        logger.debug("generate_systeminfo: detect_screen_dpi probe failed: %s", e)

    return 1.0


def detect_window_geometry(window_title: str = "Minecraft") -> dict:
    """Best-effort: try AppleScript on macOS / xdotool on Linux. Return {x,y,width,height}."""
    try:
        if sys.platform == "darwin":  # macOS
            # AppleScript to get window bounds
            script = f'''
            tell application "System Events"
                tell process "{window_title}"
                    set frontmost to true
                    set windowBounds to bounds of window 1
                    return windowBounds
                end tell
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                bounds = result.stdout.strip().split(", ")
                if len(bounds) == 4:
                    x1, y1, x2, y2 = map(int, bounds)
                    return {
                        "x": x1,
                        "y": y1,
                        "width": x2 - x1,
                        "height": y2 - y1,
                    }
        elif sys.platform.startswith("linux"):
            # Try xdotool to get window geometry
            result = subprocess.run(
                ["xdotool", "search", "--name", window_title, "getwindowgeometry", "--shell"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                geometry = {}
                for line in result.stdout.splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        geometry[key] = value
                
                if "X" in geometry and "Y" in geometry and "WIDTH" in geometry and "HEIGHT" in geometry:
                    return {
                        "x": int(geometry["X"]),
                        "y": int(geometry["Y"]),
                        "width": int(geometry["WIDTH"]),
                        "height": int(geometry["HEIGHT"]),
                    }
    except (subprocess.SubprocessError, FileNotFoundError, ValueError, KeyError) as e:
        logger.debug("generate_systeminfo: xdotool getwindowgeometry probe failed: %s", e)

    # Fallback to defaults
    return {
        "x": 0,
        "y": 0,
        "width": 1920,
        "height": 1080,
    }


def build_systeminfo(
    game_process_name: str = "minecraft.exe",
    x: int = 0,
    y: int = 0,
    width: int = 1920,
    height: int = 1080,
    record_dpi: float = 1.0,
    map_scale: float = 1.0,
    map_bounds: Optional[dict] = None,
    include_legacy_extras: bool = False,
) -> dict:
    """
    Build systeminfo.json data structure.

    PRD page 3-4 specifies systeminfo.json has EXACTLY 5 fields:
    gameProcessName, x, y, width, height, recordDpi (6 effectively — `recordDpi`
    is page 4). map_scale and map_bounds are NOT in the PDF and were added by
    an earlier sample-builder mistake. They are now opt-in via
    ``include_legacy_extras=True`` for backward compatibility.

    Args:
        game_process_name: Game process name
        x, y: Window top-left corner screen coordinates
        width, height: Game screen resolution
        record_dpi: Screen scaling ratio (1.0 / 1.5 / 2.0)
        map_scale: (legacy) Map scale factor — emitted only if
            ``include_legacy_extras=True``
        map_bounds: (legacy) Map bounds dict — emitted only if
            ``include_legacy_extras=True``
        include_legacy_extras: If True, emits the legacy ``map_scale`` and
            ``map_bounds`` fields. Default False so the production wire
            matches the PDF spec.

    Returns:
        Dictionary with systeminfo structure (PRD-canonical by default).
    """
    if record_dpi <= 0:
        raise ValueError("record_dpi must be greater than 0")

    if map_bounds is None:
        map_bounds = {
            "min_x": -10000,
            "min_z": -10000,
            "max_x": 10000,
            "max_z": 10000,
        }

    info = {
        "gameProcessName": game_process_name,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "recordDpi": record_dpi,
    }
    if include_legacy_extras:
        info["map_scale"] = map_scale
        info["map_bounds"] = map_bounds
    return info


def write_systeminfo_json(data: dict, out_path: str) -> None:
    """
    Write systeminfo.json to file.
    
    Args:
        data: Systeminfo dictionary
        out_path: Output file path
    """
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main(argv=None) -> int:
    """CLI: --output PATH + all fields as flags."""
    parser = argparse.ArgumentParser(
        description="Generate systeminfo.json file for Minecraft"
    )
    
    # Output file argument
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output JSON file path",
    )
    
    # Game process name
    parser.add_argument(
        "--game-process-name",
        default="minecraft.exe",
        help="Game process name (default: minecraft.exe)",
    )
    
    # Window geometry
    parser.add_argument(
        "--x",
        type=int,
        default=0,
        help="Window X coordinate (default: 0)",
    )
    parser.add_argument(
        "--y",
        type=int,
        default=0,
        help="Window Y coordinate (default: 0)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1920,
        help="Window width (default: 1920)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1080,
        help="Window height (default: 1080)",
    )
    
    # DPI and scale
    parser.add_argument(
        "--record-dpi",
        type=float,
        default=None,
        help="Screen scaling ratio (1.0, 1.5, 2.0). Auto-detect if not specified",
    )
    parser.add_argument(
        "--map-scale",
        type=float,
        default=1.0,
        help="Map scale factor (default: 1.0)",
    )
    
    # Map bounds
    parser.add_argument(
        "--map-bounds-min-x",
        type=int,
        default=-10000,
        help="Map bounds min X (default: -10000)",
    )
    parser.add_argument(
        "--map-bounds-min-z",
        type=int,
        default=-10000,
        help="Map bounds min Z (default: -10000)",
    )
    parser.add_argument(
        "--map-bounds-max-x",
        type=int,
        default=10000,
        help="Map bounds max X (default: 10000)",
    )
    parser.add_argument(
        "--map-bounds-max-z",
        type=int,
        default=10000,
        help="Map bounds max Z (default: 10000)",
    )
    
    # Auto-detect flags
    parser.add_argument(
        "--auto-detect-dpi",
        action="store_true",
        help="Auto-detect screen DPI",
    )
    parser.add_argument(
        "--auto-detect-window",
        action="store_true",
        help="Auto-detect Minecraft window geometry",
    )

    # PRD compliance flag: legacy extras (map_scale/map_bounds) are NOT in PDF.
    # Default off so the production wire matches the spec; explicit opt-in for
    # legacy/internal callers and back-compat tests.
    parser.add_argument(
        "--include-legacy-extras",
        action="store_true",
        help="(legacy) Emit map_scale + map_bounds (NOT in PDF spec — opt-in)",
    )
    
    args = parser.parse_args(argv)
    
    # Auto-detect DPI if requested or if record_dpi not specified
    record_dpi = args.record_dpi
    if record_dpi is None or args.auto_detect_dpi:
        record_dpi = detect_screen_dpi()
    
    # Auto-detect window geometry if requested
    x, y, width, height = args.x, args.y, args.width, args.height
    if args.auto_detect_window:
        geometry = detect_window_geometry()
        x, y, width, height = geometry["x"], geometry["y"], geometry["width"], geometry["height"]
    
    # Build map bounds dictionary
    map_bounds = {
        "min_x": args.map_bounds_min_x,
        "min_z": args.map_bounds_min_z,
        "max_x": args.map_bounds_max_x,
        "max_z": args.map_bounds_max_z,
    }
    
    # Build systeminfo data
    try:
        data = build_systeminfo(
            game_process_name=args.game_process_name,
            x=x,
            y=y,
            width=width,
            height=height,
            record_dpi=record_dpi,
            map_scale=args.map_scale,
            map_bounds=map_bounds,
            include_legacy_extras=args.include_legacy_extras,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    
    # Write to file
    try:
        write_systeminfo_json(data, args.output)
        print(f"Successfully wrote systeminfo.json to {args.output}")
        return 0
    except (IOError, OSError) as e:
        print(f"Error writing file: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
