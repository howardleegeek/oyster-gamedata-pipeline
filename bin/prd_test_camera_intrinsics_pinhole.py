#!/usr/bin/env python3
"""
PRD p3 #2: Camera Intrinsics Pinhole Validation

Validates that camera projection uses pinhole model with:
- fov (field of view) populated OR fx/fy intrinsic parameters
- aspect ratio populated OR cx/cy intrinsic parameters  
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

    # Check projection model is pinhole (if specified)
    model = projection.get("model", "")
    if model and model.lower() not in ["pinhole", ""]:
        errors.append(f"[{name}] Invalid projection model '{model}', expected 'pinhole' or empty")

    # Check for either fov/aspect OR fx/fy/cx/cy format
    has_fov_aspect = False
    has_fx_fy_cx_cy = False
    
    # Check fov/aspect format
    fov = intrinsics.get("fov", projection.get("fov"))
    aspect = intrinsics.get("aspect", projection.get("aspect"))
    
    if fov is not None and aspect is not None:
        has_fov_aspect = True
        if not isinstance(fov, (int, float)) or fov <= 0:
            errors.append(f"[{name}] Invalid 'fov' value: {fov} (must be positive number)")
        if not isinstance(aspect, (int, float)) or aspect <= 0:
            errors.append(f"[{name}] Invalid 'aspect' value: {aspect} (must be positive number)")
    elif fov is not None and aspect is None:
        errors.append(f"[{name}] Has 'fov' but missing 'aspect'")
    elif aspect is not None and fov is None:
        errors.append(f"[{name}] Has 'aspect' but missing 'fov'")
    
    # Check fx/fy/cx/cy format (action_camera.json format)
    fx = intrinsics.get("fx")
    fy = intrinsics.get("fy") 
    cx = intrinsics.get("cx")
    cy = intrinsics.get("cy")
    
    if fx is not None or fy is not None or cx is not None or cy is not None:
        has_fx_fy_cx_cy = True
        # Check all required parameters are present
        if fx is None:
            errors.append(f"[{name}] Missing required 'fx' field for pinhole intrinsics")
        elif not isinstance(fx, (int, float)) or fx <= 0:
            errors.append(f"[{name}] Invalid 'fx' value: {fx} (must be positive number)")
            
        if fy is None:
            errors.append(f"[{name}] Missing required 'fy' field for pinhole intrinsics")
        elif not isinstance(fy, (int, float)) or fy <= 0:
            errors.append(f"[{name}] Invalid 'fy' value: {fy} (must be positive number)")
            
        if cx is None:
            errors.append(f"[{name}] Missing required 'cx' field for pinhole intrinsics")
        elif not isinstance(cx, (int, float)):
            errors.append(f"[{name}] Invalid 'cx' value: {cx} (must be a number)")
            
        if cy is None:
            errors.append(f"[{name}] Missing required 'cy' field for pinhole intrinsics")
        elif not isinstance(cy, (int, float)):
            errors.append(f"[{name}] Invalid 'cy' value: {cy} (must be a number)")

    # Check for fisheye distortion parameters
    if "k1" in intrinsics or "k2" in intrinsics or "k3" in intrinsics:
        errors.append(f"[{name}] Fisheye distortion parameters (k1/k2/k3) not allowed for pinhole model")
    
    if "fisheye" in intrinsics:
        errors.append(f"[{name}] Fisheye distortion flag not allowed for pinhole model")
    
    if "distortion" in intrinsics:
        errors.append(f"[{name}] 'distortion' field not allowed for pinhole model")
    
    # Check projection for fisheye
    if projection.get("model", "").lower() == "fisheye":
        errors.append(f"[{name}] Fisheye projection model not allowed")
    
    # Ensure we have at least one valid format
    if not has_fov_aspect and not has_fx_fy_cx_cy:
        errors.append(f"[{name}] Missing required pinhole parameters. Need either fov+aspect or fx/fy/cx/cy")

    return errors


def validate_cameras_file(filepath: Path) -> Tuple[bool, List[str]]:
    """
    Validate camera intrinsics in a configuration file.

    Args:
        filepath: Path to the configuration file (JSON, JSONL, or YAML).

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
        elif suffix == ".jsonl":
            # Handle JSON Lines format
            data = []
            for line_num, line in enumerate(content.strip().split('\n'), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError as e:
                    return False, [f"Failed to parse JSONL line {line_num}: {e}"]
        else:
            # Assume JSON format
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
        # Handle action_camera.json format: list of action records
        # Or frames.jsonl format: list of frame records
        for i, record in enumerate(data):
            if isinstance(record, dict):
                # Check for camera_intrinsics field (action_camera.json format)
                if "camera_intrinsics" in record:
                    cam_name = f"action_record_{i}"
                    cameras[cam_name] = record["camera_intrinsics"]
                # Check for pose.intrinsics field (frames.jsonl format)
                elif "pose" in record and isinstance(record["pose"], dict) and "intrinsics" in record["pose"]:
                    cam_name = f"frame_{i}"
                    cameras[cam_name] = record["pose"]["intrinsics"]
                # Check if record itself has camera intrinsics fields
                elif any(field in record for field in ["fx", "fy", "cx", "cy", "fov", "aspect"]):
                    cam_name = f"record_{i}"
                    cameras[cam_name] = record
                # Otherwise, skip this record (it doesn't have camera intrinsics)
    else:
        return False, ["Invalid configuration format"]

    # If no cameras found with intrinsics, return a helpful error
    if not cameras:
        return False, ["No camera intrinsics found in file. Expected fields: camera_intrinsics, pose.intrinsics, or fx/fy/cx/cy/fov/aspect"]

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
            print("  - Valid pinhole parameters found")
            print("  - No fisheye distortion")
        return 0
    else:
        print(f"FAIL: Camera validation failed: {args.file}", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())