"""Test fixtures for the oyster_gamedata_sdk test suite."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterator

import pytest

# Make the SDK importable when running these tests directly without
# `pip install -e .`. We point at the parent of `tests/`.
HERE = Path(__file__).resolve().parent
SDK_ROOT = HERE.parent
REPO_ROOT = SDK_ROOT.parent.parent
sys.path.insert(0, str(SDK_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def sample_tarball(repo_root: Path) -> Path:
    """The official buyer-spec v1 sample tarball shipped with the repo."""
    p = repo_root / "samples" / "buyer-spec-v1-rc1.tar.gz"
    if not p.is_file():
        pytest.skip(f"sample tarball not available at {p}")
    return p


@pytest.fixture(scope="session")
def extracted_sample(sample_tarball: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Extract the sample tarball once per session and return the clip root."""
    import tarfile

    target = tmp_path_factory.mktemp("extracted_sample")
    with tarfile.open(sample_tarball, "r:*") as tf:
        if hasattr(tarfile, "data_filter"):
            tf.extractall(target, filter="data")
        else:
            tf.extractall(target)
    # Files extract at root of `target`
    return target


@pytest.fixture
def minimal_clip(tmp_path: Path) -> Path:
    """Build a synthetic, minimal but valid buyer-spec v1 clip directory.

    Useful for fast tests that don't need the 250 MB real sample.
    """
    clip = tmp_path / "synthetic_clip"
    clip.mkdir()

    # video.mp4 — non-zero but obviously fake.
    (clip / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")  # 8-byte mp4 stub

    # systeminfo.json
    (clip / "systeminfo.json").write_text(json.dumps({
        "gameProcessName": "test.exe",
        "x": 0, "y": 0,
        "width": 1920, "height": 1080,
        "recordDpi": 1.0,
        "map_scale": 1.0,
        "map_bounds": {"min_x": -100, "min_z": -100, "max_x": 100, "max_z": 100},
    }))

    # action_camera.json with 3 frames using array vector form
    frames = [
        _mk_frame(i, route_type=1 if i < 2 else 2) for i in range(3)
    ]
    (clip / "action_camera.json").write_text(json.dumps(frames))

    # gameinfo.xlsx — a real xlsx if openpyxl available, else a stub.
    try:
        import openpyxl  # type: ignore
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "GameInfo"
        ws.append(["clip_id", "duration_sec", "fps", "resolution"])
        ws.append(["synthetic-001", 300, 30, "1920x1080"])
        wb.save(str(clip / "gameinfo.xlsx"))
    except ImportError:
        (clip / "gameinfo.xlsx").write_bytes(b"PK\x03\x04")  # zip header stub

    # depth/ with one empty EXR placeholder so structural checks pass.
    depth = clip / "depth"
    depth.mkdir()
    (depth / "depth_000000.exr").write_bytes(b"v/1\x01")  # exr magic header bytes

    return clip


def _mk_frame(idx: int, *, route_type: int = 1) -> dict:
    return {
        "frame": idx,
        "time": f"2026-05-02 12:00:{idx:02d}.000",
        "fps": 30.0,
        "route_type": route_type,
        "mouse_x": 0.5,
        "mouse_y": 0.5,
        "mouse_dx": 0.0,
        "mouse_dy": 0.0,
        "keyCode": [87],
        "camera_position": [float(idx), 64.0, 0.0],
        "camera_rotation_oula": [0.0, 0.0, 0.0],
        "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
        "camera_Follow Offset": [0.0, 1.6, 0.0],
        "camera_intrinsics": {"fx": 960.0, "fy": 960.0, "cx": 960.0, "cy": 540.0},
        "camera_speed": [1.5, 0.0, 0.0],
        "player_position": [float(idx), 64.0, 0.0],
        "player_rotation_oula": [0.0, 0.0, 0.0],
        "player_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
        "player_speed": [1.5, 0.0, 0.0],
        "metric_scale": 1.0,
    }
