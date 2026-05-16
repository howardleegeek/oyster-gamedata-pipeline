#!/usr/bin/env python3
"""
PRD p3 #5: positions in meters — sanity-bound camera_position within world cube radius.

Validates camera positions in metric units are within world cube bounds.
Supports both:
- Single record format: {"camera_position": [...], "world_cube_radius": 10.0}
- action_camera.json format: [{"camera_position": [...]}, ...]
"""

import argparse
import json
import sys
import math
from typing import Any, Dict, List, Tuple

DEFAULT_WORLD_CUBE_RADIUS = 10.0  # Default 10m radius if not specified


def parse_camera_data(data: Dict[str, Any], default_radius: float = DEFAULT_WORLD_CUBE_RADIUS) -> Tuple[List[float], float]:
    """Parse camera position and world cube radius from input data.
    
    Supports both single-record format and action_camera.json list format.
    """
    # Handle list format (action_camera.json)
    if isinstance(data, list):
        if not data:
            raise ValueError("Empty list provided")
        # Use first record's camera_position
        first_record = data[0]
        if "camera_position" not in first_record:
            raise ValueError("Missing required field: camera_position")
        pos = first_record["camera_position"]
        # Use default radius for list format
        radius = default_radius
    else:
        # Single record format
        if "camera_position" not in data:
            raise ValueError("Missing required field: camera_position")
        pos = data["camera_position"]
        radius = data.get("world_cube_radius", default_radius)

    if not isinstance(pos, list) or len(pos) != 3:
        raise ValueError("camera_position must be a list of 3 numbers [x, y, z]")
    for i, v in enumerate(pos):
        if not isinstance(v, (int, float)):
            raise ValueError(f"camera_position[{i}] must be a number")
    if not isinstance(radius, (int, float)) or radius <= 0:
        raise ValueError("world_cube_radius must be a positive number")
    return [float(v) for v in pos], float(radius)


def check_bounds(position: List[float], radius: float) -> Dict[str, Any]:
    """Check if camera position is within world cube bounds (meters)."""
    x, y, z = position
    distance = math.sqrt(x * x + y * y + z * z)
    within_bounds = distance <= radius
    return {
        "camera_position_meters": position,
        "world_cube_radius_meters": radius,
        "distance_from_origin": round(distance, 6),
        "within_bounds": within_bounds,
        "status": "VALID" if within_bounds else "OUT_OF_BOUNDS"
    }


def main(argv: List[str]) -> int:
    """Main CLI entry point for camera position validation."""
    parser = argparse.ArgumentParser(
        description="Validate camera position within world cube radius (meters)"
    )
    parser.add_argument("--input", "-i", help="Input JSON file (stdin if omitted)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--strict", action="store_true", help="Require cube bounds")
    parser.add_argument("--radius", "-r", type=float, default=DEFAULT_WORLD_CUBE_RADIUS,
                        help=f"World cube radius in meters (default: {DEFAULT_WORLD_CUBE_RADIUS})")
    try:
        args = parser.parse_args(argv[1:])
        if args.input:
            with open(args.input, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.load(sys.stdin)
        position, radius = parse_camera_data(data, args.radius)
        result = check_bounds(position, radius)
        if args.strict:
            x, y, z = position
            within_cube = all(abs(c) <= radius for c in [x, y, z])
            result["within_cube"] = within_cube
            result["status"] = "VALID" if within_cube else "OUT_OF_BOUNDS"
        if args.verbose:
            output = result
        else:
            output = {"valid": result["within_bounds"], "status": result["status"],
                      "distance_meters": result["distance_from_origin"]}
        print(json.dumps(output, indent=2))
        return 0 if result["within_bounds"] else 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
