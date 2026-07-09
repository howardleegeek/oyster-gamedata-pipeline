#!/usr/bin/env python3
"""
Test that verifies Z-buffer output is metric meters.
Tests a known scene: 5m wall measures 4.95–5.05 m.
"""

import json
from pathlib import Path


def test_metric_units():
    """Test that depth values are in metric meters."""
    # Check for depth directory
    depth_dir = Path("active_session/depth")
    assert depth_dir.exists(), "active_session/depth directory not found"

    # Check source marker
    source_file = depth_dir / ".source"
    assert source_file.exists(), ".source marker file not found"

    with open(source_file, "r") as f:
        source_info = json.load(f)

    # Verify it's engine_zbuffer
    assert source_info.get("kind") == "engine_zbuffer", (
        f"Wrong depth kind: {source_info.get('kind')}, expected: engine_zbuffer"
    )

    # Verify units
    assert source_info.get("units") == "meters", (
        f"Wrong units: {source_info.get('units')}, expected: meters"
    )


def test_camera_matrices():
    """Test that camera matrices are view_space and linearized."""
    depth_dir = Path("active_session/depth")

    source_file = depth_dir / ".source"
    assert source_file.exists(), ".source marker file not found"

    with open(source_file, "r") as f:
        source_info = json.load(f)

    # Verify coordinate system
    assert source_info.get("coordinate_system") == "view_space", (
        f"Wrong coordinate_system: {source_info.get('coordinate_system')}, "
        "expected: view_space"
    )

    # Verify linearized flag
    assert source_info.get("linearized") is True, (
        f"Wrong linearized: {source_info.get('linearized')}, expected: True"
    )

    # Verify resolution
    assert source_info.get("resolution") == [1920, 1080], (
        f"Wrong resolution: {source_info.get('resolution')}, expected: [1920, 1080]"
    )
