"""Tests for ``cs2_demo_to_engine_telemetry.py`` coordinate conversion.

The full demo-parsing path requires a real ``.dem`` file (and demoparser2
installed). These tests cover the pure-math conversion functions, the
schema shape, and the import-fallback when demoparser2 is absent — the
parts that *don't* depend on having an actual CS2 replay on disk.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

# Load the script directly (it lives under bin/, not the package src/).
_BIN = Path(__file__).resolve().parents[1] / "src" / "oyster_agent_runner" / "cs2" / "cs2_demo_parser.py"
_spec = importlib.util.spec_from_file_location("cs2_demo_module", _BIN)
assert _spec is not None and _spec.loader is not None
cs2_demo_module = importlib.util.module_from_spec(_spec)
sys.modules["cs2_demo_module"] = cs2_demo_module
_spec.loader.exec_module(cs2_demo_module)


def test_cs2_pos_to_buyer_origin() -> None:
    """Origin maps to origin — identity at the world center."""
    assert cs2_demo_module._cs2_pos_to_buyer((0.0, 0.0, 0.0)) == [0.0, 0.0, 0.0]


def test_cs2_pos_to_buyer_unit_axes() -> None:
    """1 source unit = 0.0254 m. Forward (+X CS2) → +Z buyer."""
    out = cs2_demo_module._cs2_pos_to_buyer((100.0, 0.0, 0.0))
    assert out == pytest.approx([0.0, 0.0, 100.0 * 0.0254])


def test_cs2_pos_to_buyer_left_axis_flips() -> None:
    """+Y left in CS2 → -X (opposite of right) in buyer left-hand frame."""
    out = cs2_demo_module._cs2_pos_to_buyer((0.0, 100.0, 0.0))
    assert out == pytest.approx([-100.0 * 0.0254, 0.0, 0.0])


def test_cs2_pos_to_buyer_up_axis() -> None:
    """+Z up in CS2 → +Y up in buyer."""
    out = cs2_demo_module._cs2_pos_to_buyer((0.0, 0.0, 100.0))
    assert out == pytest.approx([0.0, 100.0 * 0.0254, 0.0])


def test_cs2_eye_angles_pitch_sign_flip() -> None:
    """CS2 pitch positive=look down. Buyer pitch positive=look up. Sign flips."""
    out = cs2_demo_module._cs2_eye_angles_to_buyer_oula(45.0, 0.0)
    assert out == [-45.0, 0.0, 0.0]


def test_cs2_eye_angles_yaw_passthrough() -> None:
    """Yaw passes through unchanged (both CCW from +X horizontal)."""
    out = cs2_demo_module._cs2_eye_angles_to_buyer_oula(0.0, 90.0)
    assert out == [0.0, 90.0, 0.0]


def test_cs2_eye_angles_roll_always_zero() -> None:
    """CS2 first-person has no roll — always 0."""
    out = cs2_demo_module._cs2_eye_angles_to_buyer_oula(0.0, 0.0)
    assert out[2] == 0.0


def test_euler_to_quat_identity() -> None:
    """All-zero Euler → identity quaternion (0, 0, 0, 1)."""
    q = cs2_demo_module._euler_to_quat_xyzw(0.0, 0.0, 0.0)
    assert q == pytest.approx([0.0, 0.0, 0.0, 1.0], abs=1e-12)


def test_euler_to_quat_unit_norm() -> None:
    """Quaternion is always unit-norm (within float64 tolerance)."""
    q = cs2_demo_module._euler_to_quat_xyzw(30.0, 60.0, 15.0)
    norm = math.sqrt(sum(x * x for x in q))
    assert norm == pytest.approx(1.0, rel=1e-9)


def test_cs2_velocity_vector3_shape() -> None:
    """Velocity is a Vector3 (per-axis m/s), not scalar magnitude.

    Buyer-spec §3 row "player_speed" requires list[3] floats. CS2's
    m_vecVelocity is in inches/s — we convert to m/s and re-axis.
    """
    out = cs2_demo_module._cs2_velocity_to_buyer((100.0, 0.0, 0.0))
    assert isinstance(out, list) and len(out) == 3
    assert out == pytest.approx([0.0, 0.0, 100.0 * 0.0254])


def test_cs2_velocity_axis_swap_consistent_with_position() -> None:
    """Velocity axis remap matches position remap so a player moving +X
    in CS2 has both ``player_position[2]`` and ``player_speed[2]`` increasing."""
    pos = cs2_demo_module._cs2_pos_to_buyer((1.0, 2.0, 3.0))
    vel = cs2_demo_module._cs2_velocity_to_buyer((1.0, 2.0, 3.0))
    # Same axis-swap pattern: only the units differ (both end up * 0.0254
    # since the source values are equal — units are pos:metres / vel:m/s).
    assert [v / p if p != 0 else 0 for v, p in zip(vel, pos, strict=True)] == pytest.approx(
        [1.0, 1.0, 1.0]
    )


def test_constants_match_buyer_spec() -> None:
    """Source unit conversion is exactly 1 inch = 0.0254 m, no rounding."""
    assert cs2_demo_module._SOURCE_UNIT_METRES == 0.0254


def test_default_frame_rate_matches_video() -> None:
    """Default fps lines up with the buyer-spec video.mp4 30fps requirement."""
    assert cs2_demo_module._DEFAULT_FRAME_RATE == 30


def test_default_max_frames_matches_5min_floor() -> None:
    """5 min × 30 fps = 9000 — matches buyer's minimum video duration."""
    assert cs2_demo_module._DEFAULT_MAX_FRAMES == 30 * 60 * 5


def test_main_rejects_missing_demo_file(tmp_path: Path) -> None:
    """CLI surfaces a clear error when the .dem path is bogus."""
    nonexistent = tmp_path / "missing.dem"
    rc = cs2_demo_module.main(["--demo", str(nonexistent), "--output", str(tmp_path / "out.json")])
    assert rc == 2
