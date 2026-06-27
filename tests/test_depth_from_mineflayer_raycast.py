#!/usr/bin/env python3
"""Tests for ``bin/depth_from_mineflayer_raycast.py``.

The depth raycaster must produce REAL geometric depth — no placeholders.
We test:

  1. Happy path: 5-frame metadata.jsonl, bot at (0, 70, 0) looking down →
     centre-of-frame depth ≈ 6 m (= 70 - 64 ground plane).
  2. Bot looking horizontally → centre-of-frame depth = max_depth (flat
     ground never intersects a horizontal ray).
  3. Missing metadata.jsonl → FileNotFoundError.
  4. Content-uniqueness: when the bot moves frame-to-frame, ≥ 50% of the
     emitted EXR sha256 hashes are unique (depth varies with pose).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

# numpy + OpenEXR are gating the tests — skip the whole module on environments
# where the EXR writer can't run, mirroring the pattern in
# tests/bin/test_real_depth_filler.py.
np = pytest.importorskip("numpy")
OpenEXR = pytest.importorskip("OpenEXR")
Imath = pytest.importorskip("Imath")

from bin.depth_from_mineflayer_raycast import (
    DEFAULT_MAX_DEPTH_M,
    render_depth_for_session,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_observation_event(
    timestamp: float,
    pos: tuple[float, float, float],
    yaw_rad: float,
    pitch_rad: float,
) -> dict:
    """Build an OBSERVATION-shaped event matching the format produced by
    ``src/oyster_agent_runner/trajectory_logger.py`` and consumed by
    ``buyer_spec_adapter._extract_observation``."""
    return {
        "event_type": "OBSERVATION",
        "timestamp": timestamp,
        "event_args": {
            "value": {
                "type": "observation",
                "ok": True,
                "tick": int(timestamp * 20),
                "bot": {
                    "position": [pos[0], pos[1], pos[2]],
                    "yaw": yaw_rad,
                    "pitch": pitch_rad,
                    "health": 20,
                    "food": 20,
                    "xp": 0,
                },
                "inventory": [],
                "blocks_near": [],
                "entities_near": [],
            },
        },
    }


def _write_metadata_jsonl(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fp:
        for ev in events:
            fp.write(json.dumps(ev) + "\n")


def _read_exr_z_channel(path: Path, width: int, height: int) -> "np.ndarray":
    """Read the float32 ``Z`` channel from a single-channel EXR back into a
    ``(H, W)`` numpy array — used to verify per-pixel depth values, not just
    file existence."""
    inp = OpenEXR.InputFile(str(path))
    try:
        pt = Imath.PixelType(Imath.PixelType.FLOAT)
        raw = inp.channel("Z", pt)
    finally:
        inp.close()
    arr = np.frombuffer(raw, dtype=np.float32).reshape(height, width).copy()
    return arr


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


# Use a small image to keep raycast tests fast (1920x1080 × 5 frames at
# 64m / 0.25m steps = 8M ray-steps × 5 = blocks the test runner). 64x48
# preserves the 4:3 aspect roughly while keeping the test under 5s.
TEST_W = 64
TEST_H = 48


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHappyPathLookingDown:
    """Bot at (0, 70, 0) looking straight down → centre depth ≈ 6 m."""

    def test_center_pixel_distance_to_ground(self, tmp_path: Path) -> None:
        meta = tmp_path / "metadata.jsonl"
        out_dir = tmp_path / "depth"
        # Pitch = +π/2 → looking straight down (Mineflayer: +pitch is down).
        events = [
            _make_observation_event(
                timestamp=i * (1.0 / 30.0),
                pos=(0.0, 70.0, 0.0),
                yaw_rad=0.0,
                pitch_rad=math.pi / 2,
            )
            for i in range(5)
        ]
        _write_metadata_jsonl(meta, events)

        hashes = render_depth_for_session(
            meta,
            out_dir,
            width=TEST_W,
            height=TEST_H,
            fov_degrees=70.0,
            fps=30,
            max_depth_meters=DEFAULT_MAX_DEPTH_M,
            chunk_data=None,
        )

        assert len(hashes) == 5
        for i in range(5):
            assert (out_dir / f"frame_{i:06d}.exr").is_file()

        # Centre pixel of frame 0 looks straight down. Bot y=70, ground y=64,
        # so the first 0.25-block step that crosses ``y <= 64`` is at
        # t = 6.0 m exactly (70 - 64). Allow one step of tolerance for
        # marching alignment.
        depth = _read_exr_z_channel(out_dir / "frame_000000.exr", TEST_W, TEST_H)
        cx, cy = TEST_W // 2, TEST_H // 2
        assert depth[cy, cx] == pytest.approx(6.0, abs=0.30), (
            f"expected ~6 m to ground when looking straight down from y=70, got {depth[cy, cx]:.3f}"
        )
        # Sky should be impossible (camera looks down) — every pixel should
        # have hit the ground within ``max_depth``. The diagonal corners go
        # at an angle, so they hit slightly farther — but never max_depth.
        assert (depth < DEFAULT_MAX_DEPTH_M).all(), (
            "every pixel should hit the ground when looking straight down"
        )


class TestBotLookingHorizontally:
    """Pitch=0 yaw=0 → all rays parallel-or-above ground → no hits."""

    def test_all_pixels_max_depth(self, tmp_path: Path) -> None:
        meta = tmp_path / "metadata.jsonl"
        out_dir = tmp_path / "depth"
        # Bot well above the ground (y=120) and looking dead horizontal
        # (pitch=0). With ``GROUND_Y=64`` the bot is 56 m above the ground
        # plane, so even the bottommost row of pixels — which dips below
        # horizontal by atan((H/2)/fy) — should not reach the ground within
        # ``max_depth=64`` for our small test image.
        events = [
            _make_observation_event(
                timestamp=0.0,
                pos=(0.0, 120.0, 0.0),
                yaw_rad=0.0,
                pitch_rad=0.0,
            ),
        ]
        _write_metadata_jsonl(meta, events)

        hashes = render_depth_for_session(
            meta,
            out_dir,
            width=TEST_W,
            height=TEST_H,
            fov_degrees=70.0,
            fps=30,
            max_depth_meters=DEFAULT_MAX_DEPTH_M,
            chunk_data=None,
        )

        assert len(hashes) == 1
        depth = _read_exr_z_channel(out_dir / "frame_000000.exr", TEST_W, TEST_H)

        # Centre row points exactly horizontal — those rays NEVER drop below
        # GROUND_Y=64, so depth must equal max_depth there. ``np.allclose``
        # because the EXR round-trip is float32 and DEFAULT_MAX_DEPTH_M is
        # python float — bit-exact equality is not guaranteed.
        cy = TEST_H // 2
        assert np.allclose(depth[cy, :], DEFAULT_MAX_DEPTH_M, atol=1e-4), (
            f"centre row should be all max_depth ({DEFAULT_MAX_DEPTH_M}), "
            f"got min={depth[cy, :].min()}, max={depth[cy, :].max()}"
        )

        # And given the camera is at y=120 with vertical FOV 70°, even the
        # bottom-of-image ray drops at angle (H/2)/fy from horizontal. With
        # H=48, fy = (48/2) / tan(35°) ≈ 34.3, so the steepest pixel drops
        # at angle atan(24/34.3) ≈ 35°, and at max_depth=64 m it has fallen
        # 64 sin(35°) ≈ 36.7 m → from y=120 that's y ≈ 83 — still well above
        # GROUND_Y=64, so NO pixel hits ground. Whole image must be sky.
        assert np.allclose(depth, DEFAULT_MAX_DEPTH_M, atol=1e-4), (
            "every pixel should be max_depth (sky) when bot is high above "
            "ground and looking horizontal"
        )


class TestMissingMetadataRaisesFileNotFound:
    """Caller hands us a path that does not exist → FileNotFoundError."""

    def test_missing_file(self, tmp_path: Path) -> None:
        ghost = tmp_path / "this_does_not_exist.jsonl"
        out_dir = tmp_path / "depth"

        with pytest.raises(FileNotFoundError):
            render_depth_for_session(ghost, out_dir, width=TEST_W, height=TEST_H)

    def test_empty_metadata_raises_runtime_error(self, tmp_path: Path) -> None:
        """Existing-but-empty file is a different failure mode — not a
        missing path, but a session with zero usable observations. The
        contract says RuntimeError, not FileNotFoundError, so the caller
        can distinguish "you gave me the wrong path" from "the recorder
        captured nothing"."""
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        out_dir = tmp_path / "depth"

        with pytest.raises(RuntimeError, match="no OBSERVATION events"):
            render_depth_for_session(empty, out_dir, width=TEST_W, height=TEST_H)


class TestContentUniquenessAcrossMovement:
    """When the bot moves frame-to-frame, depth maps must differ — verified
    by sha256 set having ≥ 0.5 × frame_count unique entries."""

    def test_moving_bot_emits_unique_depth(self, tmp_path: Path) -> None:
        meta = tmp_path / "metadata.jsonl"
        out_dir = tmp_path / "depth"
        # Bot looking down at varying altitudes: y = 70, 71, 72, 73, 74. Each
        # frame's centre-pixel depth is (y - 64) = different value, so each
        # EXR has different bytes.
        events = []
        for i in range(6):
            events.append(
                _make_observation_event(
                    timestamp=i * (1.0 / 30.0),
                    pos=(float(i), 70.0 + float(i), 0.0),
                    yaw_rad=0.0,
                    pitch_rad=math.pi / 2,
                )
            )
        _write_metadata_jsonl(meta, events)

        hashes = render_depth_for_session(
            meta,
            out_dir,
            width=TEST_W,
            height=TEST_H,
            fov_degrees=70.0,
            fps=30,
            max_depth_meters=DEFAULT_MAX_DEPTH_M,
            chunk_data=None,
        )

        assert len(hashes) == 6
        unique = set(hashes.values())
        # Acceptance criterion #4 from the task: ≥ 50% of the hashes are
        # distinct when the bot moves. We expect 100% here because every
        # altitude is different, but the test asserts the floor.
        assert len(unique) >= max(1, int(0.5 * len(hashes))), (
            f"expected ≥50% unique EXR hashes when bot moves, got "
            f"{len(unique)}/{len(hashes)} unique"
        )


class TestStationaryBotEmitsIdenticalFrames:
    """Sanity check the inverse: when the bot does NOT move, the EXR bytes
    are identical — proves we are not injecting any per-frame randomness."""

    def test_static_pose_repeats(self, tmp_path: Path) -> None:
        meta = tmp_path / "metadata.jsonl"
        out_dir = tmp_path / "depth"
        events = [
            _make_observation_event(
                timestamp=i * (1.0 / 30.0),
                pos=(0.0, 70.0, 0.0),
                yaw_rad=0.0,
                pitch_rad=math.pi / 2,
            )
            for i in range(3)
        ]
        _write_metadata_jsonl(meta, events)

        hashes = render_depth_for_session(
            meta,
            out_dir,
            width=TEST_W,
            height=TEST_H,
            fov_degrees=70.0,
            fps=30,
            max_depth_meters=DEFAULT_MAX_DEPTH_M,
            chunk_data=None,
        )

        # All three frames captured identical pose → identical depth →
        # identical EXR bytes → identical sha256.
        assert len(set(hashes.values())) == 1, (
            "stationary bot should yield byte-identical depth EXRs"
        )
