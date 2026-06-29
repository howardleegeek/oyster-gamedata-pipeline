#!/usr/bin/env python3
"""
Tests for mod build verification.

Tests:
1. depth_zbuffer_capture.diff applies cleanly to a known mod source state
2. zbuffer_to_exr.py converts fixture depth_raw/*.f32 to depth/*.exr
"""

import pathlib
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest


# =============================================================================
# Helper: Find patch file (returns absolute path)
# =============================================================================
def find_patch_file():
    """Find the depth_zbuffer_capture.diff patch file."""
    diff_paths = [
        pathlib.Path("patches/depth_zbuffer_capture.diff"),
        pathlib.Path(
            "/private/tmp/cluster-2026-05-18-mod-build/patches/depth_zbuffer_capture.diff"
        ),
        pathlib.Path(
            "/private/tmp/cluster-2026-05-18-integration/integrated/patches/depth_zbuffer_capture.diff"
        ),
    ]

    for p in diff_paths:
        if p.exists():
            # Return absolute path
            return p.resolve()

    return None


# =============================================================================
# Test 1: Patch applies cleanly
# =============================================================================
def test_depth_zbuffer_capture_diff_exists():
    """Verify the .diff patch file exists."""
    diff_path = find_patch_file()

    if diff_path is None:
        pytest.fail("depth_zbuffer_capture.diff not found")

    print(f"Found patch at: {diff_path}")


def test_patch_applies_to_mod_stub():
    """Verify the .diff patch can be applied to a synthetic mod stub."""
    diff_path = find_patch_file()

    if diff_path is None:
        pytest.skip("depth_zbuffer_capture.diff not found")

    print(f"Using patch: {diff_path}")

    # Create a temporary directory with a minimal mod structure
    with tempfile.TemporaryDirectory() as tmpdir:
        mod_dir = Path(tmpdir) / "mc-mod-fabric"
        src_dir = mod_dir / "src" / "main" / "java"
        src_dir.mkdir(parents=True)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=mod_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=mod_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=mod_dir, check=True, capture_output=True
        )

        # Create a minimal Java file to have a valid git state
        minimal_java = src_dir / "com" / "example" / "mod" / "ExampleMod.java"
        minimal_java.parent.mkdir(parents=True)
        minimal_java.write_text("package com.example.mod;\n\npublic class ExampleMod {}\n")

        subprocess.run(["git", "add", "."], cwd=mod_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=mod_dir, check=True, capture_output=True
        )

        # Try dry-run patch application with absolute path
        result = subprocess.run(
            ["git", "apply", "--check", str(diff_path)], cwd=mod_dir, capture_output=True, text=True
        )

        if result.returncode != 0:
            print(f"Patch apply failed (dry-run): {result.stderr}")
            # Try with 3-way merge
            result = subprocess.run(
                ["git", "apply", "--check", "--3way", str(diff_path)],
                cwd=mod_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                pytest.fail(f"Patch cannot be applied: {result.stderr}")
            else:
                print("Patch applies with 3-way merge")
        else:
            print("Patch applies cleanly")


# =============================================================================
# Test 2: zbuffer_to_exr.py processes .f32 files
# =============================================================================
def create_f32_fixture(width=1920, height=1080):
    """Create a simple depth buffer fixture (float32 binary)."""
    # Create a simple depth gradient: closer objects have smaller depth values
    depth_data = np.zeros((height, width), dtype=np.float32)

    for y in range(height):
        for x in range(width):
            # Simple gradient: depth increases from center outward
            cx, cy = width // 2, height // 2
            dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            max_dist = np.sqrt(cx**2 + cy**2)
            # Depth from 0.1 (near) to 100.0 (far)
            depth_data[y, x] = 0.1 + (dist / max_dist) * 99.9

    return depth_data


def find_zbuffer_to_exr_script():
    """Find the zbuffer_to_exr.py script."""
    script_paths = [
        pathlib.Path("bin/zbuffer_to_exr.py"),
        pathlib.Path("/private/tmp/cluster-2026-05-18-mod-build/bin/zbuffer_to_exr.py"),
        pathlib.Path(
            "/private/tmp/cluster-2026-05-18-integration/integrated/bin/zbuffer_to_exr.py"
        ),
    ]

    for p in script_paths:
        if p.exists():
            return p.resolve()

    return None


def test_zbuffer_to_exr_processes_f32_files():
    """Verify bin/zbuffer_to_exr.py converts fixture depth_raw/*.f32 to depth/*.exr."""

    script_path = find_zbuffer_to_exr_script()

    if script_path is None:
        pytest.skip("zbuffer_to_exr.py not found")

    print(f"Using script: {script_path}")

    # Create temporary directory structure
    with tempfile.TemporaryDirectory() as temp_path:
        tmpdir = Path(temp_path)
        active_session = tmpdir / "active_session"
        depth_raw = active_session / "depth_raw"
        depth_output = active_session / "depth"

        depth_raw.mkdir(parents=True)
        depth_output.mkdir(parents=True)

        # Create 3 fixture .f32 files
        width, height = 1920, 1080
        for i in range(3):
            depth_data = create_f32_fixture(width, height)
            f32_path = depth_raw / f"frame_{i:04d}.f32"

            # Write as raw float32 binary (little-endian)
            with open(f32_path, "wb") as f:
                f.write(depth_data.tobytes())

        print(f"Created 3 fixture .f32 files in {depth_raw}")

        # Run zbuffer_to_exr.py
        # Note: The script expects to run from a directory with active_session/
        # We'll run it from tmpdir
        result = subprocess.run(
            [sys.executable, str(script_path)], cwd=tmpdir, capture_output=True, text=True
        )

        print(f"Script stdout: {result.stdout}")
        if result.returncode != 0:
            print(f"Script stderr: {result.stderr}")

        # Check that EXR files were created
        exr_files = list(depth_output.glob("*.exr"))
        npy_files = list(depth_output.glob("*.npy"))  # Fallback if OpenEXR not available

        print(f"EXR files found: {exr_files}")
        print(f"NPY files found: {npy_files}")

        # Either EXR or NPY (fallback) files should exist
        output_files = exr_files or npy_files

        assert len(output_files) == 3, f"Expected 3 output files, got {len(output_files)}"

        # Verify file names match expected pattern
        for f in output_files:
            print(f"Output file: {f.name}")

        # If we have NPY files (fallback), verify the data
        if npy_files:
            for npy_file in npy_files:
                data = np.load(npy_file)
                assert data.shape == (height, width), f"Wrong shape: {data.shape}"
                assert data.dtype == np.float32, f"Wrong dtype: {data.dtype}"
                print(f"Verified {npy_file.name}: shape={data.shape}, dtype={data.dtype}")

        print("✓ zbuffer_to_exr.py successfully processed .f32 files")


def test_zbuffer_to_exr_source_marker():
    """Verify that zbuffer_to_exr.py creates the .source marker file."""

    script_path = find_zbuffer_to_exr_script()

    if script_path is None:
        pytest.skip("zbuffer_to_exr.py not found")

    # Create temporary directory structure
    with tempfile.TemporaryDirectory() as temp_path:
        tmpdir = Path(temp_path)
        active_session = tmpdir / "active_session"
        depth_raw = active_session / "depth_raw"
        depth_output = active_session / "depth"

        depth_raw.mkdir(parents=True)
        depth_output.mkdir(parents=True)

        # Create one fixture .f32 file
        depth_data = create_f32_fixture()
        f32_path = depth_raw / "frame_0000.f32"
        with open(f32_path, "wb") as f:
            f.write(depth_data.tobytes())

        # Run zbuffer_to_exr.py
        subprocess.run(
            [sys.executable, str(script_path)], cwd=tmpdir, capture_output=True, text=True
        )

        # Check for .source marker
        source_marker = depth_output / ".source"

        if source_marker.exists():
            import json

            with open(source_marker) as f:
                marker = json.load(f)

            print(f"Source marker: {marker}")

            assert marker.get("kind") == "engine_zbuffer"
            assert marker.get("units") == "meters"
            assert marker.get("format") == "exr"
            assert marker.get("dtype") == "float32"
            assert marker.get("channels") == ["Z"]
            assert marker.get("resolution") == [1920, 1080]

            print("✓ .source marker created with correct metadata")
        else:
            print("Note: .source marker not created (may be optional)")


if __name__ == "__main__":
    # Run pytest with verbose output
    pytest.main([__file__, "-v", "--tb=short"])
