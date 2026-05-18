#!/usr/bin/env python3
"""Smoke test for the zbuffer pipeline (D1+D2+D3 closed loop).

Creates a fake session directory with synthetic data, runs the H8 audit
patch, and asserts PASS.  No external fixtures or real session required.

Usage:
    python3 bin/zbuffer_pipeline_smoke.py
"""

import json
import pathlib
import struct
import sys
import tempfile

import numpy as np


def _write_tick_bin(path: pathlib.Path, tick_id: int, w: int = 8, h: int = 8):
    """Write a raw f32 depth tick file with 12-byte header (W, H, tick_id)."""
    # Simulate depth values in 5–50 m range
    rng = np.random.default_rng(tick_id)
    depth = rng.uniform(5.0, 50.0, size=(h, w)).astype(np.float32)
    header = struct.pack("<III", w, h, tick_id)
    with open(path, "wb") as f:
        f.write(header)
        f.write(depth.tobytes())


def _write_game_state_jsonl(path: pathlib.Path, num_ticks: int = 5):
    """Write game_state.jsonl with ticks at 50 ms intervals."""
    with open(path, "w") as f:
        for i in range(num_ticks):
            record = {
                "tick_id": i,
                "timestamp_ms": i * 50,
                "player_pos": [0.0, 0.0, 0.0],
                "action": "idle",
            }
            f.write(json.dumps(record) + "\n")


def _write_action_camera_jsonl(path: pathlib.Path, num_frames: int = 10):
    """Write action_camera_*.jsonl with frames at 16.67 ms (60 fps)."""
    with open(path, "w") as f:
        for i in range(num_frames):
            record = {
                "frame_id": i,
                "timestamp_ms": round(i * 16.67, 2),
                "fov_deg": 90.0,
                "resolution": [1920, 1080],
            }
            f.write(json.dumps(record) + "\n")


def _write_exr(path: pathlib.Path):
    """Write a minimal 1×1 black EXR with channel 'Z'."""
    import Imath
    import OpenEXR

    width, height = 1, 1
    # Single black pixel (0.0) as half float
    pixel_data = struct.pack("<e", 0.0)  # half-float zero

    header = OpenEXR.Header(width, height)
    half_chan = Imath.Channel(Imath.PixelType(Imath.PixelType.HALF))
    header["channels"] = {"Z": half_chan}

    exr = OpenEXR.OutputFile(str(path), header)
    exr.writePixels({"Z": pixel_data})
    exr.close()


def _write_depth_source(path: pathlib.Path, frame_count: int = 10):
    """Write depth/.source JSON."""
    source = {
        "kind": "engine_zbuffer",
        "framerate": 60,
        "max_depth_m": 256.0,
        "calibrated": True,
        "frame_count": frame_count,
        "alignment_method": "nearest_tick_50ms",
        "gap_misses": 0,
        "gap_miss_ratio": "0/0",
    }
    with open(path, "w") as f:
        json.dump(source, f)


def main():
    with tempfile.TemporaryDirectory(prefix="zbuffer_smoke_") as tmpdir:
        session = pathlib.Path(tmpdir)

        # --- D1: game state ---
        _write_game_state_jsonl(session / "game_state.jsonl", num_ticks=5)

        # --- D1: action camera ---
        _write_action_camera_jsonl(session / "action_camera_0.jsonl", num_frames=10)

        # --- D2: tick bin files ---
        tick_dir = session / "zbuffer"
        tick_dir.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            _write_tick_bin(tick_dir / f"tick_{i}.bin", tick_id=i)

        # --- D2: depth source + EXRs ---
        depth_dir = session / "depth"
        depth_dir.mkdir(parents=True, exist_ok=True)
        _write_depth_source(depth_dir / ".source", frame_count=10)
        for i in range(10):
            _write_exr(depth_dir / f"frame_{i}.exr")

        # --- D3: H8 audit ---
        # Add bin/ to sys.path so we can import the patch module
        bin_dir = pathlib.Path(__file__).resolve().parent
        if str(bin_dir) not in sys.path:
            sys.path.insert(0, str(bin_dir))

        from prd_compliance_audit_H8_patch import evaluate_h8

        result = evaluate_h8(session)
        print(json.dumps(result, indent=2))

        if result["status"] != "PASS":
            print(f"SMOKE FAIL: expected PASS, got {result['status']}", file=sys.stderr)
            print(f"Evidence: {result['evidence']}", file=sys.stderr)
            sys.exit(1)

        print("SMOKE OK: H8 audit PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
