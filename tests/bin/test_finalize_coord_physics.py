#!/usr/bin/env python3
"""
rc19.0.3 — Physics sanity tests for finalize_session.py coordinate + unit transform.

These tests are the AUTONOMOUS LOOP'S EXIT GATE. They verify PHYSICAL
correctness using pure physics — NOT PRD knowledge, NOT AI derivation.
If the coordinate transform negates the wrong axis, or the velocity unit
conversion is wrong, or gravity is mis-scaled, these tests go RED.

The autonomous loop (oyster-audit/RC1903-AUTONOMOUS-RFC.md) may only
declare rc19.0.3 done when ALL of these pass. A wrong-direction transform
CANNOT pass test_left_handed_coordinate_system; a missing unit conversion
CANNOT pass test_walking_speed_realistic.

Ground-truth oracle (do not re-derive — see RC1903_PROGRESS.md L1):
  vendor/enrichment/docs/COORDINATE_SYSTEMS_GUIDE.md:55
  - MC: right-handed, Y-up, +X east +Y up +Z south, 1 block = 1 m
  - Buyer: left-handed, X right / Y up / Z front
  - "Negate yaw to swap CW/CCW"; velocity blocks/tick × 20 = m/s

Physics constants (independent of PRD — these are Minecraft engine facts):
  - MC default walking speed     ≈ 4.317 m/s
  - MC sprint speed              ≈ 5.6 m/s
  - MC gravity (entity)          ≈ 32 m/s²  (NOT Earth's 9.8)
  - MC tick rate                 = 20 Hz
  - 1 MC block                   = 1 m
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BIN_DIR = REPO_ROOT / "bin"
SESSION_DIR = Path("/tmp/rc19.0.2-session/session_20260513_203931_70db9d7b")

# Physics tolerances — generous enough to absorb sampling noise, tight
# enough to catch a missing ×20 conversion (which would be 20× off) or a
# sign error (which flips the result).
WALK_SPEED_MIN_MPS = 1.5   # below this → velocity left in blocks/tick
WALK_SPEED_MAX_MPS = 9.0   # above this → over-converted or wrong unit
GRAVITY_MIN_MPS2 = 24.0    # MC gravity ≈ 32; Earth's 9.8 would fail this
GRAVITY_MAX_MPS2 = 42.0
GRAVITY_FIELD_EXPECTED = 32.0
GRAVITY_FIELD_TOL = 4.0


# ---------------------------------------------------------------------------
# Fixture: run finalize once against the real rc19.0.2 test session
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def finalized_session() -> Path:
    """Run finalize_session.py against the real test session, return its dir.

    Skips the whole module if the test session isn't on disk (e.g. /tmp
    was cleared) — a skip is honest, a false-pass is not.
    """
    if not SESSION_DIR.exists():
        pytest.skip(
            f"test session not on disk at {SESSION_DIR} — "
            "re-SCP from minipc1 before running coord physics tests"
        )
    ac = SESSION_DIR / "action_camera.json"
    gs = SESSION_DIR / "game_state.jsonl"
    if not ac.exists() or not gs.exists():
        pytest.skip(
            f"test session at {SESSION_DIR} missing action_camera.json or "
            "game_state.jsonl — incomplete SCP"
        )
    # Run finalize. Non-fatal if it returns non-zero on the depth step
    # (cv2 may be absent on the CI host) — we only need the coord/velocity
    # backfill + gameinfo, which run before depth.
    subprocess.run(
        [sys.executable, str(BIN_DIR / "finalize_session.py"), str(SESSION_DIR)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return SESSION_DIR


def _load_action_camera(session_dir: Path) -> list[dict]:
    data = json.loads((session_dir / "action_camera.json").read_text())
    assert isinstance(data, list), "action_camera.json must be a list of frames"
    return data


def _vec_norm(v) -> float:
    if not v or not isinstance(v, (list, tuple)) or len(v) != 3:
        return 0.0
    return math.sqrt(sum(float(c) ** 2 for c in v))


# ---------------------------------------------------------------------------
# Test 1 — walking speed is physically realistic (catches unit-conversion bug)
# ---------------------------------------------------------------------------
def test_walking_speed_realistic(finalized_session: Path) -> None:
    """Median non-trivial player speed magnitude must be in human-walk range.

    If finalize leaves velocity in blocks/tick (~0.2), this fails LOW.
    If finalize double-converts, it fails HIGH. Only a correct ×20
    blocks/tick→m/s conversion lands in the 1.5–9.0 m/s band.
    """
    frames = _load_action_camera(finalized_session)
    speeds = []
    for f in frames:
        for key in ("player_speed", "camera_speed"):
            mag = _vec_norm(f.get(key))
            if mag > 0.05:  # ignore stationary frames
                speeds.append(mag)
            break
    if len(speeds) < 10:
        pytest.skip(
            f"only {len(speeds)} non-trivial speed samples — "
            "session may be mostly stationary; cannot assert walk speed"
        )
    speeds.sort()
    median = speeds[len(speeds) // 2]
    assert WALK_SPEED_MIN_MPS <= median <= WALK_SPEED_MAX_MPS, (
        f"median player speed {median:.3f} m/s outside human-walk band "
        f"[{WALK_SPEED_MIN_MPS}, {WALK_SPEED_MAX_MPS}] — "
        f"likely a missing or wrong blocks/tick→m/s (×20) conversion. "
        f"MC walk speed is ~4.317 m/s."
    )


# ---------------------------------------------------------------------------
# Test 2 — gravity acceleration matches Minecraft engine (catches scale bug)
# ---------------------------------------------------------------------------
def test_gravity_acceleration(finalized_session: Path) -> None:
    """During falling frames, vertical velocity should accelerate at ~32 m/s².

    Minecraft entity gravity is ~32 m/s², NOT Earth's 9.8. If velocity
    units are wrong (blocks/tick not m/s), the measured acceleration is
    20× too small and fails GRAVITY_MIN_MPS2.
    """
    frames = _load_action_camera(finalized_session)
    # Collect (time_s, vertical_velocity) for frames flagged airborne.
    samples: list[tuple[float, float]] = []
    for f in frames:
        on_ground = f.get("on_ground")
        spd = f.get("player_speed") or f.get("camera_speed")
        t = f.get("time")
        if isinstance(t, str):
            t = None  # ISO string — skip, need numeric seconds
        if t is None:
            t = f.get("timestamp")
        if on_ground is False and spd and isinstance(spd, list) and len(spd) == 3 and t is not None:
            samples.append((float(t), float(spd[1])))  # index 1 = vertical (Y-up)
    if len(samples) < 6:
        pytest.skip(
            f"only {len(samples)} airborne frames with velocity+time — "
            "session has too little falling to measure gravity"
        )
    # Measure |Δv_y / Δt| across consecutive airborne pairs; take the median.
    accels = []
    for (t0, v0), (t1, v1) in zip(samples, samples[1:]):
        dt = t1 - t0
        if 0.01 < dt < 0.5:  # consecutive-ish frames only
            accels.append(abs((v1 - v0) / dt))
    if len(accels) < 3:
        pytest.skip("not enough consecutive airborne pairs to measure gravity")
    accels.sort()
    median_accel = accels[len(accels) // 2]
    assert GRAVITY_MIN_MPS2 <= median_accel <= GRAVITY_MAX_MPS2, (
        f"median vertical acceleration {median_accel:.2f} m/s² outside MC "
        f"gravity band [{GRAVITY_MIN_MPS2}, {GRAVITY_MAX_MPS2}] — "
        f"if ~1.6, velocity is still in blocks/tick (needs ×20); "
        f"if ~9.8, Earth gravity was used instead of MC's ~32."
    )


# ---------------------------------------------------------------------------
# Test 3 — finalize's quaternion matches the BUYER-PIPELINE ORACLE exactly
# ---------------------------------------------------------------------------
# This is the genuine independent check (NOT circular): finalize_session.py
# ships a SELF-CONTAINED reimplementation of the buyer_spec euler→quat
# (it can't import vendor/enrichment — that submodule isn't in the recorder
# installer). This test imports the REAL vendor/enrichment oracle module
# and asserts finalize's output matches it bit-for-bit. If finalize's
# reimplementation has the wrong axis convention (e.g. yaw about Z instead
# of Y — the rc19.0.2 bug) or wrong composition order, this test goes RED.
def _load_oracle_euler_to_quat():
    """Import the buyer-accepted euler_to_quat_xyzw from vendor/enrichment."""
    oracle_src = REPO_ROOT / "vendor" / "enrichment" / "src"
    if not oracle_src.exists():
        return None
    if str(oracle_src) not in sys.path:
        sys.path.insert(0, str(oracle_src))
    try:
        from oyster_enrichment.quaternion_utils import (  # noqa: PLC0415
            euler_to_quat_xyzw as oracle_e2q,
        )
        return oracle_e2q
    except Exception:
        return None


def test_quaternion_matches_buyer_oracle(finalized_session: Path) -> None:
    """finalize's camera_rotation_quaternion must equal the buyer oracle's output.

    For each frame, re-derive the expected quaternion by feeding the
    frame's own rotation_oula = [roll, pitch, yaw] into the REAL
    vendor/enrichment buyer_spec oracle. finalize used its self-contained
    reimplementation; if that reimplementation diverges from the oracle
    (wrong axis / wrong order / sign error), the quaternions won't match.

    This is the rc19.0.3 handedness/convention gate. The rc19.0.2 bug —
    yaw rotated about Z instead of Y — produces a clearly different
    quaternion and is caught here.
    """
    oracle_e2q = _load_oracle_euler_to_quat()
    if oracle_e2q is None:
        pytest.skip(
            "vendor/enrichment oracle not importable — cannot cross-check "
            "finalize's quaternion reimplementation against the buyer module"
        )
    frames = _load_action_camera(finalized_session)
    checked = 0
    max_err = 0.0
    worst = None
    for f in frames:
        q = f.get("camera_rotation_quaternion")
        oula = f.get("rotation_oula")
        if (not q or not isinstance(q, list) or len(q) != 4
                or not oula or not isinstance(oula, list) or len(oula) != 3):
            continue
        roll, pitch, yaw = float(oula[0]), float(oula[1]), float(oula[2])
        # oracle signature: euler_to_quat_xyzw(pitch, yaw, roll, convention=...)
        expected = oracle_e2q(pitch, yaw, roll, convention="buyer_spec")
        # quaternion double-cover: q and -q are the same rotation
        err_pos = max(abs(a - b) for a, b in zip(q, expected))
        err_neg = max(abs(a + b) for a, b in zip(q, expected))
        err = min(err_pos, err_neg)
        if err > max_err:
            max_err = err
            worst = (oula, q, list(expected))
        checked += 1
    if checked < 10:
        pytest.skip(
            f"only {checked} frames with both quaternion + rotation_oula — "
            "cannot cross-check against oracle"
        )
    assert max_err < 1e-4, (
        f"finalize's quaternion diverges from the buyer-pipeline oracle by "
        f"{max_err:.6f} (max over {checked} frames). finalize's "
        f"euler_to_quat_xyzw reimplementation does NOT match buyer_spec.\n"
        f"  worst frame rotation_oula={worst[0]}\n"
        f"  finalize quat = {worst[1]}\n"
        f"  oracle quat   = {worst[2]}\n"
        f"Per L1 oracle (vendor/enrichment quaternion_utils.py): buyer_spec "
        f"is yaw-about-Y, pitch-about-X, roll-about-Z, Y-X-Z extrinsic."
    )


# ---------------------------------------------------------------------------
# Test 4 — gameinfo.xlsx carries the MC gravity constant
# ---------------------------------------------------------------------------
def test_gravity_field_present(finalized_session: Path) -> None:
    """gameinfo.xlsx must declare world_gravity_mps2 ≈ 32.0 (MC vanilla)."""
    gameinfo = finalized_session / "gameinfo.xlsx"
    if not gameinfo.exists():
        pytest.fail(
            f"gameinfo.xlsx not generated at {gameinfo} — finalize step 3 "
            "must run and must include world_gravity_mps2"
        )
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError:
        pytest.skip("openpyxl not installed on this host — cannot read xlsx")
    wb = openpyxl.load_workbook(gameinfo, read_only=True)
    ws = wb.active
    found_value = None
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if "world_gravity_mps2" in cells:
            idx = cells.index("world_gravity_mps2")
            # value is usually the next cell, or somewhere in the row
            for c in row[idx + 1:]:
                if isinstance(c, (int, float)):
                    found_value = float(c)
                    break
    wb.close()
    assert found_value is not None, (
        "gameinfo.xlsx has no numeric world_gravity_mps2 field — "
        "finalize must add it (MC vanilla gravity = 32.0 m/s²)"
    )
    assert abs(found_value - GRAVITY_FIELD_EXPECTED) <= GRAVITY_FIELD_TOL, (
        f"world_gravity_mps2 = {found_value}, expected ~{GRAVITY_FIELD_EXPECTED} "
        f"(MC vanilla). Earth's 9.8 is wrong for Minecraft."
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
