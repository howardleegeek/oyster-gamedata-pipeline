#!/usr/bin/env python3
"""
PRD p3 #2: Camera Intrinsics Pinhole Validation

Validates that camera projection uses pinhole model with:
- fov (field of view) populated
- aspect ratio populated
- No fisheye distortion parameters

Exit codes:
    0: All cameras pass validation
    1: One or more cameras failed validation
    2: File read/parse error
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def validate_pinhole_intrinsics(camera: Dict[str, Any], name: str) -> List[str]:
    """
    Validate a single camera's intrinsics for pinhole model compliance.

    Args:
        camera: Dictionary containing camera intrinsics data.
        name: Identifier for the camera (for error messages).

    Returns:
        List of validation error messages (empty if valid).
    """
    errors: List[str] = []

    intrinsics = camera.get("intrinsics", camera)
    projection = intrinsics.get("projection", {})

    # Check projection model is pinhole
    model = projection.get("model", "")
    if model and model.lower() != "pinhole":
        errors.append(f"[{name}] Invalid projection model '{model}', expected 'pinhole'")

    # Check fov is populated
    fov = intrinsics.get("fov", projection.get("fov"))
    if fov is None:
        errors.append(f"[{name}] Missing required 'fov' field")
    elif not isinstance(fov, (int, float)) or fov <= 0:
        errors.append(f"[{name}] Invalid 'fov' value: {fov} (must be positive number)")

    # Check aspect ratio is populated
    aspect = intrinsics.get("aspect", projection.get("aspect"))
    if aspect is None:
        errors.append(f"[{name}] Missing required 'aspect' field")
    elif not isinstance(aspect, (int, float)) or aspect <= 0:
        errors.append(f"[{name}] Invalid 'aspect' value: {aspect} (must be positive number)")

    # Check for fisheye distortion parameters (must NOT be present)
    fisheye_keys = ["fisheye", "fisheye_coefficients", "distortion_fisheye",
                    "ftheta", "fisheye_params", "k1_fisheye", "fisheye_model"]
    for key in fisheye_keys:
        if key in intrinsics or key in projection:
            errors.append(f"[{name}] Forbidden fisheye parameter '{key}' found")

    # Check distortion model if present
    distortion = intrinsics.get("distortion", projection.get("distortion", {}))
    if isinstance(distortion, dict):
        dist_model = distortion.get("model", "")
        if dist_model and "fisheye" in dist_model.lower():
            errors.append(f"[{name}] Fisheye distortion model not allowed: '{dist_model}'")

    return errors


def validate_cameras_file(filepath: Path) -> Tuple[bool, List[str]]:
    """
    Load and validate cameras from a JSON or YAML file.

    Args:
        filepath: Path to the camera configuration file.

    Returns:
        Tuple of (success, list of error messages).
    """
    errors: List[str] = []

    try:
        content = filepath.read_text()
    except OSError as e:
        return False, [f"Failed to read file: {e}"]

    # Parse based on extension
    suffix = filepath.suffix.lower()
    try:
        if suffix in (".yaml", ".yml"):
            if yaml is None:
                return False, ["PyYAML not available for YAML parsing"]
            data = yaml.safe_load(content)
        else:
            data = json.loads(content)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        return False, [f"Failed to parse file: {e}"]

    if data is None:
        return False, ["Empty configuration file"]

    # Handle different data structures
    cameras: Dict[str, Any] = {}
    if isinstance(data, dict):
        if "cameras" in data:
            cameras = data["cameras"]
        else:
            cameras = data
    elif isinstance(data, list):
        cameras = {f"camera_{i}": cam for i, cam in enumerate(data)}
    else:
        return False, ["Invalid configuration format"]

    # Validate each camera
    for cam_name, cam_data in cameras.items():
        if isinstance(cam_data, dict):
            cam_errors = validate_pinhole_intrinsics(cam_data, cam_name)
            errors.extend(cam_errors)
        else:
            errors.append(f"[{cam_name}] Invalid camera data format")

    return len(errors) == 0, errors


def main(argv: List[str] | None = None) -> int:
    """
    Main entry point for camera intrinsics validation.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        description="Validate camera intrinsics for pinhole projection model"
    )
    parser.add_argument(
        "file", type=Path, help="Path to camera configuration file (JSON/YAML)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Print detailed validation results"
    )
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        return 2

    success, errors = validate_cameras_file(args.file)

    if success:
        print(f"PASS: All cameras validated as pinhole model: {args.file}")
        if args.verbose:
            print("  - fov: present")
            print("  - aspect: present")
            print("  - fisheye params: none")
        return 0
    else:
        print(f"FAIL: Camera validation failed: {args.file}", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
