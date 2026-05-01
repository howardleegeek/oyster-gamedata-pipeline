"""Tests for the Phase 1 → buyer-spec v1 adapter.

Coverage matrix:

* Synthetic Phase 1 bundle is adapted into a 4-file deliverable layout.
* Mineflayer position is correctly converted to the buyer's left-hand frame
  (``buyer_x = -mc_x``, others identity).
* yaw/pitch (radians) → buyer Euler degrees → quaternion → Euler round-trip.
* ``metric_scale == 1.0`` for every record (Minecraft block ≈ 1 m).
* Camera intrinsics match the pinhole derivation at FOV = 70°,
  ``1920×1080``.
* Empty bundle (no observations) yields an empty action_camera record list
  but still emits the other three deliverables.
* CLI ``adapt-buyer-spec`` smoke + missing-bundle error exit code.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from oyster_agent_runner.buyer_spec_adapter import (
    ACTION_CAMERA_FILENAME,
    BUYER_SPEC_FIELDS,
    DEFAULT_FOLLOW_OFFSET,
    DEFAULT_VIDEO_HEIGHT,
    DEFAULT_VIDEO_WIDTH,
    DELIVERABLE_COUNT,
    GAMEINFO_FILENAME,
    MANIFEST_OUT_FILENAME,
    MINECRAFT_DEFAULT_FOV_DEG,
    MINECRAFT_METRIC_SCALE,
    SYSTEMINFO_FILENAME,
    adapt_phase1_to_buyer_spec,
    camera_intrinsics_for_minecraft,
    euler_to_quat_xyzw,
    minecraft_position_to_buyer,
    minecraft_yaw_pitch_to_buyer_oula,
)
from oyster_agent_runner.cli import app
from oyster_agent_runner.minecraft_streams import MinecraftStreamWriter
from oyster_agent_runner.schema import (
    EVENT_ACTION,
    EVENT_AGENT_STEP,
    EVENT_END,
    EVENT_OBSERVATION,
    EVENT_START,
    TrajectoryEvent,
)

# --- Fixtures ---------------------------------------------------------------


def _write_bundle_with_observations(
    bundle_dir: Path,
    obs_payloads: list[dict[str, Any]],
) -> Path:
    """Build a Phase 1 bundle that carries explicit Mineflayer-shaped observations.

    Each entry in ``obs_payloads`` becomes one ``OBSERVATION`` event with
    ``event_args.value`` set to the payload — matching what the runner
    writes when the env returns a structured dict observation.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    anchor = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    with MinecraftStreamWriter(bundle_dir) as w:
        w.write(
            TrajectoryEvent(
                timestamp=0.0,
                event_type=EVENT_START,
                event_args={"task_id": "ADAPT-test"},
            )
        )
        for i, payload in enumerate(obs_payloads):
            ts = 0.1 * (i + 1)
            w.write(
                TrajectoryEvent(
                    timestamp=ts,
                    event_type=EVENT_OBSERVATION,
                    event_args={"value": payload},
                )
            )
            w.write(
                TrajectoryEvent(
                    timestamp=ts,
                    event_type=EVENT_ACTION,
                    event_args={"op": "noop"},
                )
            )
            w.write(
                TrajectoryEvent(
                    timestamp=ts,
                    event_type=EVENT_AGENT_STEP,
                    event_args={"step": i, "success": False},
                )
            )
        w.write(
            TrajectoryEvent(
                timestamp=0.1 * (len(obs_payloads) + 1),
                event_type=EVENT_END,
                event_args={
                    "success": True,
                    "total_steps": len(obs_payloads),
                    "reason": "success",
                },
            )
        )
        w.finalize_manifest(
            task_id="ADAPT-test",
            model="claude-sonnet-4-5",
            provider="claude-thinking",
            environment="minecraft",
            anchor_utc=anchor,
            success=True,
            termination_reason="success",
            total_steps=len(obs_payloads),
            wall_clock_sec=0.5,
            thinking_budget_tokens=16000,
            license="train-only",
        )
    return bundle_dir


@pytest.fixture
def basic_bundle(tmp_path: Path) -> Path:
    """A two-observation Mineflayer-shaped bundle."""
    return _write_bundle_with_observations(
        tmp_path / "bundle",
        [
            {
                "position": {"x": 12.5, "y": 64.0, "z": -8.0},
                "yaw": 0.0,
                "pitch": 0.0,
                "health": 20.0,
            },
            {
                "position": {"x": 12.5, "y": 64.0, "z": -7.0},
                "yaw": 0.5,
                "pitch": -0.1,
                "health": 20.0,
            },
        ],
    )


@pytest.fixture
def empty_bundle(tmp_path: Path) -> Path:
    """A Phase 1 bundle with zero observations (only START/END markers)."""
    return _write_bundle_with_observations(tmp_path / "empty", [])


# --- Pure helper functions -------------------------------------------------


def test_minecraft_position_negates_x_for_handedness() -> None:
    """``buyer_x = -mc_x``, ``buyer_y = mc_y``, ``buyer_z = mc_z``."""
    out = minecraft_position_to_buyer(10.0, 64.0, -8.0)
    assert out == [-10.0, 64.0, -8.0]


def test_minecraft_position_zero_origin() -> None:
    out = minecraft_position_to_buyer(0.0, 0.0, 0.0)
    assert out == [0.0, 0.0, 0.0]


def test_minecraft_yaw_pitch_levels() -> None:
    """At yaw=0, pitch=0: result is ``[0, 0, 0]``."""
    pitch_deg, yaw_deg, roll_deg = minecraft_yaw_pitch_to_buyer_oula(0.0, 0.0)
    assert pitch_deg == pytest.approx(0.0)
    assert yaw_deg == pytest.approx(0.0)
    assert roll_deg == pytest.approx(0.0)


def test_minecraft_yaw_pitch_pitch_sign_flips() -> None:
    """Minecraft pitch=+0.5 rad ("look down") → buyer pitch ≈ -28.6 deg."""
    pitch_deg, yaw_deg, _ = minecraft_yaw_pitch_to_buyer_oula(0.0, 0.5)
    assert pitch_deg == pytest.approx(-math.degrees(0.5))
    assert yaw_deg == pytest.approx(0.0)


def test_minecraft_yaw_pitch_yaw_keeps_sign() -> None:
    """Minecraft yaw rad in → buyer yaw deg in; sign preserved."""
    _, yaw_deg, _ = minecraft_yaw_pitch_to_buyer_oula(math.pi / 4, 0.0)
    assert yaw_deg == pytest.approx(45.0)


def test_camera_intrinsics_at_default_fov() -> None:
    """fx = (width/2) / tan(70°/2) at 1920×1080."""
    intr = camera_intrinsics_for_minecraft()
    expected_fx = (DEFAULT_VIDEO_WIDTH / 2.0) / math.tan(
        math.radians(MINECRAFT_DEFAULT_FOV_DEG) / 2.0
    )
    assert intr["fx"] == pytest.approx(expected_fx, rel=1e-9)
    assert intr["fy"] == pytest.approx(expected_fx, rel=1e-9)
    assert intr["cx"] == DEFAULT_VIDEO_WIDTH / 2.0
    assert intr["cy"] == DEFAULT_VIDEO_HEIGHT / 2.0


def test_camera_intrinsics_rejects_bad_inputs() -> None:
    intr = camera_intrinsics_for_minecraft(width=0, height=1080, fov_deg=70.0)
    assert intr == {"fx": 0.0, "fy": 0.0, "cx": 0.0, "cy": 0.0}


def test_euler_quat_round_trip_identity() -> None:
    """Euler (0,0,0) → quaternion (0,0,0,1) within float64 tolerance."""
    q = euler_to_quat_xyzw(0.0, 0.0, 0.0)
    assert len(q) == 4
    assert q[0] == pytest.approx(0.0, abs=1e-9)
    assert q[1] == pytest.approx(0.0, abs=1e-9)
    assert q[2] == pytest.approx(0.0, abs=1e-9)
    assert q[3] == pytest.approx(1.0, abs=1e-9)


def test_euler_quat_unit_norm() -> None:
    """All produced quaternions are unit norm."""
    for pitch, yaw, roll in [(30.0, 45.0, 0.0), (-89.0, 12.0, 0.0), (0.0, 180.0, 0.0)]:
        q = euler_to_quat_xyzw(pitch, yaw, roll)
        norm = math.sqrt(sum(c * c for c in q))
        assert norm == pytest.approx(1.0, abs=1e-9)


def test_yaw_pitch_to_quat_to_euler_round_trip() -> None:
    """Mineflayer yaw/pitch → buyer oula → quaternion → euler ≈ original oula."""
    # Use a non-trivial Mineflayer angle.
    mc_yaw = math.pi / 6  # 30°
    mc_pitch = -math.pi / 12  # -15° (looking up in MC)
    oula = minecraft_yaw_pitch_to_buyer_oula(mc_yaw, mc_pitch)
    q = euler_to_quat_xyzw(oula[0], oula[1], oula[2])
    # Round-trip via the euler decomposer if available; otherwise re-derive
    # via the quaternion → R → euler path baked into stdlib.
    pitch_back, yaw_back, roll_back = _quat_xyzw_to_oula(q)
    assert pitch_back == pytest.approx(oula[0], abs=1e-6)
    assert yaw_back == pytest.approx(oula[1], abs=1e-6)
    assert roll_back == pytest.approx(0.0, abs=1e-6)
    # Roll input was 0 → output is 0.
    assert roll_back == pytest.approx(0.0, abs=1e-6)


def _quat_xyzw_to_oula(xyzw: list[float]) -> tuple[float, float, float]:
    """Decompose XYZW → (pitch, yaw, roll) deg via Z-X-Y intrinsic matrix.

    Matches the *implementation* of
    ``oyster_enrichment.quaternion_utils.quat_xyzw_to_euler`` (which uses
    ``sin_pitch = r21`` for ``R = R_z @ R_x @ R_y``) so tests pass with or
    without C8 on the path. Pure stdlib.
    """
    x, y, z, w = xyzw
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm

    r00 = 1.0 - 2.0 * (y * y + z * z)
    r01 = 2.0 * (x * y - w * z)
    r02 = 2.0 * (x * z + w * y)
    r11 = 1.0 - 2.0 * (x * x + z * z)
    r20 = 2.0 * (x * z - w * y)
    r21 = 2.0 * (y * z + w * x)
    r22 = 1.0 - 2.0 * (x * x + y * y)

    sin_pitch = max(-1.0, min(1.0, r21))
    pitch = math.asin(sin_pitch)
    cos_pitch = math.cos(pitch)
    if cos_pitch > 1e-6:
        yaw = math.atan2(-r20, r22)
        roll = math.atan2(-r01, r11)
    else:
        yaw = math.atan2(r02, r00)
        roll = 0.0
    return math.degrees(pitch), math.degrees(yaw), math.degrees(roll)


# --- adapt_phase1_to_buyer_spec end-to-end ---------------------------------


def test_adapter_produces_four_deliverables(basic_bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    files = sorted(p.name for p in output.iterdir())
    assert ACTION_CAMERA_FILENAME in files
    assert SYSTEMINFO_FILENAME in files
    assert GAMEINFO_FILENAME in files
    assert MANIFEST_OUT_FILENAME in files
    assert len(files) == DELIVERABLE_COUNT


def test_adapter_action_camera_records_have_all_fields(basic_bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    records = json.loads((output / ACTION_CAMERA_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(records, list)
    assert len(records) == 2  # two observations in fixture
    for rec in records:
        assert set(rec.keys()) == set(BUYER_SPEC_FIELDS)


def test_adapter_player_position_left_hand_conversion(basic_bundle: Path, tmp_path: Path) -> None:
    """First observation is at (mc_x=12.5, mc_y=64, mc_z=-8) → (-12.5, 64, -8)."""
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    records = json.loads((output / ACTION_CAMERA_FILENAME).read_text(encoding="utf-8"))
    assert records[0]["player_position"] == [-12.5, 64.0, -8.0]


def test_adapter_camera_position_is_player_plus_follow_offset(
    basic_bundle: Path, tmp_path: Path
) -> None:
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    records = json.loads((output / ACTION_CAMERA_FILENAME).read_text(encoding="utf-8"))
    pp = records[0]["player_position"]
    cp = records[0]["camera_position"]
    expected = [
        pp[0] + DEFAULT_FOLLOW_OFFSET[0],
        pp[1] + DEFAULT_FOLLOW_OFFSET[1],
        pp[2] + DEFAULT_FOLLOW_OFFSET[2],
    ]
    assert cp == expected


def test_adapter_metric_scale_is_one_per_record(basic_bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    records = json.loads((output / ACTION_CAMERA_FILENAME).read_text(encoding="utf-8"))
    for rec in records:
        assert rec["metric_scale"] == MINECRAFT_METRIC_SCALE == 1.0


def test_adapter_intrinsics_match_pinhole_at_70_deg(basic_bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    records = json.loads((output / ACTION_CAMERA_FILENAME).read_text(encoding="utf-8"))
    expected = camera_intrinsics_for_minecraft()
    assert records[0]["camera_intrinsics"] == pytest.approx(expected)


def test_adapter_quaternion_round_trip_via_oula(basic_bundle: Path, tmp_path: Path) -> None:
    """Quaternion in record decodes back to the recorded euler."""
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    records = json.loads((output / ACTION_CAMERA_FILENAME).read_text(encoding="utf-8"))
    rec = records[1]  # has non-zero yaw / pitch
    oula = rec["player_rotation_oula"]
    quat = rec["player_rotation_quaternion"]
    pitch_back, yaw_back, roll_back = _quat_xyzw_to_oula(quat)
    assert pitch_back == pytest.approx(oula[0], abs=1e-6)
    assert yaw_back == pytest.approx(oula[1], abs=1e-6)
    assert roll_back == pytest.approx(oula[2], abs=1e-6)


def test_adapter_camera_speed_first_frame_zero(basic_bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    records = json.loads((output / ACTION_CAMERA_FILENAME).read_text(encoding="utf-8"))
    assert records[0]["camera_speed"] == 0.0


def test_adapter_camera_speed_finite_difference(basic_bundle: Path, tmp_path: Path) -> None:
    """Bot moves +1 in mc_z over 0.1s → buyer speed ≈ 10 m/s."""
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    records = json.loads((output / ACTION_CAMERA_FILENAME).read_text(encoding="utf-8"))
    # mc_z went from -8 to -7 (delta = +1 m), dt = 0.1s
    assert records[1]["camera_speed"] == pytest.approx(10.0, rel=1e-6)


def test_adapter_systeminfo_default_geometry(basic_bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    sysinfo = json.loads((output / SYSTEMINFO_FILENAME).read_text(encoding="utf-8"))
    assert sysinfo["width"] == DEFAULT_VIDEO_WIDTH
    assert sysinfo["height"] == DEFAULT_VIDEO_HEIGHT
    assert sysinfo["gameProcessName"] == "Minecraft"
    assert sysinfo["recordDpi"] == 1.0


def test_adapter_gameinfo_carries_manifest_meta(basic_bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    gameinfo = json.loads((output / GAMEINFO_FILENAME).read_text(encoding="utf-8"))
    assert gameinfo["task_id"] == "ADAPT-test"
    assert gameinfo["model"] == "claude-sonnet-4-5"
    assert gameinfo["provider"] == "claude-thinking"
    assert gameinfo["fov_deg"] == MINECRAFT_DEFAULT_FOV_DEG
    assert gameinfo["metric_scale"] == 1.0
    assert gameinfo["follow_offset"] == list(DEFAULT_FOLLOW_OFFSET)


def test_adapter_manifest_passthrough_preserves_task_id(basic_bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "buyer_out"
    adapt_phase1_to_buyer_spec(basic_bundle, output)
    manifest = json.loads((output / MANIFEST_OUT_FILENAME).read_text(encoding="utf-8"))
    assert manifest["task_id"] == "ADAPT-test"
    assert manifest["phase"] == 1
    assert manifest["alignment"]["metadata_event_count"] >= 2


def test_adapter_empty_bundle_emits_empty_records(empty_bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "empty_out"
    adapt_phase1_to_buyer_spec(empty_bundle, output)
    records = json.loads((output / ACTION_CAMERA_FILENAME).read_text(encoding="utf-8"))
    assert records == []
    # All four deliverables still present.
    assert (output / SYSTEMINFO_FILENAME).exists()
    assert (output / GAMEINFO_FILENAME).exists()
    assert (output / MANIFEST_OUT_FILENAME).exists()


def test_adapter_rejects_missing_bundle_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="bundle directory not found"):
        adapt_phase1_to_buyer_spec(tmp_path / "nope", tmp_path / "out")


def test_adapter_rejects_missing_manifest(tmp_path: Path) -> None:
    bundle = tmp_path / "broken"
    bundle.mkdir()
    with pytest.raises(FileNotFoundError, match="missing manifest"):
        adapt_phase1_to_buyer_spec(bundle, tmp_path / "out")


# --- CLI integration -------------------------------------------------------


def test_cli_adapt_buyer_spec_smoke(basic_bundle: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "cli_out"
    result = runner.invoke(
        app,
        [
            "adapt-buyer-spec",
            "--bundle",
            str(basic_bundle),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (output / ACTION_CAMERA_FILENAME).exists()
    assert (output / SYSTEMINFO_FILENAME).exists()
    assert (output / GAMEINFO_FILENAME).exists()
    assert (output / MANIFEST_OUT_FILENAME).exists()


def test_cli_adapt_buyer_spec_missing_bundle(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "adapt-buyer-spec",
            "--bundle",
            str(tmp_path / "nope"),
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 2
    assert "cannot adapt bundle" in result.output
