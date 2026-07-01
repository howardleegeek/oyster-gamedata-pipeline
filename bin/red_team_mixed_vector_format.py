#!/usr/bin/env python3
"""
Red team: Mixed Vector3 format detection - lint rejects format inconsistency.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any, Union

Vector3Dict = dict[str, float]
Vector3List = list[float]
Vector3 = Union[Vector3Dict, Vector3List]


def parse_vector3_dict(data: dict[str, Any]) -> Vector3Dict:
    """Parse Vector3 from dict format: {'x': float, 'y': float, 'z': float}."""
    return {
        "x": float(data.get("x", 0.0)),
        "y": float(data.get("y", 0.0)),
        "z": float(data.get("z", 0.0)),
    }


def parse_vector3_list(data: list[Any]) -> Vector3List:
    """Parse Vector3 from list format: [x, y, z]."""
    if len(data) != 3:
        raise ValueError(f"Vector3 list must have exactly 3 elements, got {len(data)}")
    return [float(data[0]), float(data[1]), float(data[2])]


def vector3_to_dict(v: Vector3) -> Vector3Dict:
    """Convert any Vector3 format to dict."""
    if isinstance(v, dict):
        return {"x": v["x"], "y": v["y"], "z": v["z"]}
    return {"x": v[0], "y": v[1], "z": v[2]}


def vector3_to_list(v: Vector3) -> Vector3List:
    """Convert any Vector3 format to list."""
    if isinstance(v, list):
        return [v[0], v[1], v[2]]
    return [v["x"], v["y"], v["z"]]


# Red team test case: mixed dict and list formats in same file
SAMPLE_POSITIONS: list[dict[str, Vector3]] = [
    {"position": {"x": 1.0, "y": 2.0, "z": 3.0}},  # dict format
    {"position": [4.0, 5.0, 6.0]},  # list format - MIXED!
    {"position": {"x": 7.0, "y": 8.0, "z": 9.0}},  # dict format
    {"position": [10.0, 11.0, 12.0]},  # list format - MIXED!
]


def validate_format_consistency(positions: list[dict[str, Vector3]]) -> tuple[bool, list[str]]:
    """Validate all Vector3 entries use consistent format."""
    errors: list[str] = []
    format_counts: dict[str, int] = {"dict": 0, "list": 0}

    for idx, entry in enumerate(positions):
        pos = entry.get("position")
        if pos is None:
            errors.append(f"Entry {idx}: missing 'position' key")
            continue

        if isinstance(pos, dict):
            format_counts["dict"] += 1
            if not {"x", "y", "z"}.issubset(pos.keys()):
                errors.append(f"Entry {idx}: dict missing required keys")
        elif isinstance(pos, (list, tuple)):
            format_counts["list"] += 1
            if len(pos) != 3:
                errors.append(f"Entry {idx}: list must have 3 elements")
        else:
            errors.append(f"Entry {idx}: unsupported type {type(pos).__name__}")

    if format_counts["dict"] > 0 and format_counts["list"] > 0:
        errors.append(
            f"Format inconsistency: {format_counts['dict']} dict, {format_counts['list']} list"
        )
        return False, errors

    return len(errors) == 0, errors


def analyze_file(filepath: Path) -> int:
    """Analyze a Python file for Vector3 format consistency."""
    try:
        source = filepath.read_text()
        tree = ast.parse(source)
    except (SyntaxError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Extract 'positions' variable from the AST
    positions: list[dict[str, Vector3]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "positions":
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Dict):
                                entry: dict[str, Vector3] = {}
                                for key, value in zip(elt.keys, elt.values):
                                    if isinstance(key, ast.Constant) and key.value == "position":
                                        # Try to extract the position value
                                        if isinstance(value, ast.Dict):
                                            pos_dict: Vector3Dict = {}
                                            for k, v in zip(value.keys, value.values):
                                                if isinstance(k, ast.Constant) and isinstance(v, (ast.Constant, ast.Num)):
                                                    pos_dict[k.value if isinstance(k, ast.Constant) else (k.n if hasattr(k, 'n') else str(k))] = v.value if isinstance(v, ast.Constant) else (v.n if hasattr(v, 'n') else 0.0)
                                            entry["position"] = pos_dict
                                        elif isinstance(value, (ast.List, ast.Tuple)):
                                            pos_list: Vector3List = []
                                            for v in value.elts:
                                                if isinstance(v, (ast.Constant, ast.Num)):
                                                    pos_list.append(v.value if isinstance(v, ast.Constant) else (v.n if hasattr(v, 'n') else 0.0))
                                            entry["position"] = pos_list
                                if entry:
                                    positions.append(entry)

    if not positions:
        print(f"Warning: No 'positions' variable found in {filepath}", file=sys.stderr)
        return 1

    print(f"Analyzing {filepath}...")
    is_valid, errors = validate_format_consistency(positions)

    if is_valid:
        print("✓ Format is consistent")
        return 0
    print("✗ Format inconsistency detected:")
    for error in errors:
        print(f"  - {error}")
    return 1


def main(argv: list[str] | None = None) -> int:
    """Main entry point for Vector3 format validation CLI."""
    parser = argparse.ArgumentParser(description="Red team: Detect mixed Vector3 format")
    parser.add_argument("file", nargs="?", type=Path, help="Python file to analyze")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    if args.file:
        return analyze_file(args.file)

    print("Analyzing sample Vector3 data...")
    is_valid, errors = validate_format_consistency(SAMPLE_POSITIONS)

    if is_valid:
        print("✓ Format is consistent")
        return 0
    print("✗ Format inconsistency detected:")
    for error in errors:
        print(f"  - {error}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
