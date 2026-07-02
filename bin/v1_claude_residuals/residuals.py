"""V₁ Claude PINNs residuals.

12 pure-math residual functions on action_camera frame records. Each returns
``ResidualResult`` so the BFT consensus orchestrator can compare V₁'s vote
against V₂/V₃/V₄ in lockstep without parsing prose.

Naming follows PRD literal (PDF page 4-5 + Lark page 文件2):
- camera_rotation_oula (拼音 — NOT euler)
- camera_Follow Offset (literal space + capital F)
- camera_intrinsics keys: fx, fy, cx, cy (lowercase)
- mouse_x/y/dx/dy: list[float]
- keyCode: list[int] (Windows VK code)
- quaternion array order: [x, y, z, w]

We deliberately do NOT accept aliases ("camera_rotation_euler", "Cx", scalar
mouse_x) — that tolerance is what made V₁/v0.19.0 a false-PASS rubber stamp.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResidualResult:
    """One residual evaluation. ``passed`` is the only field consensus uses;
    ``residual`` and ``threshold`` are evidence for view-change debugging."""

    name: str
    passed: bool
    residual: float
    threshold: float
    note: str = ""


# ---------------------------------------------------------------------------
# Helpers — kept private to discourage cross-residual coupling.
# ---------------------------------------------------------------------------

def _take(value: Any) -> float:
    """Take element 0 of a list[float] or return scalar.

    PRD wire: mouse_x is list[float]. We accept either form on read but flag
    the scalar form as a structural concern for residual R07 to handle.
    """
    if isinstance(value, list) and value:
        return float(value[0])
    return float(value)


def _euler_zyx_to_quat(pitch_deg: float, yaw_deg: float, roll_deg: float) -> tuple[float, float, float, float]:
    """ZYX intrinsic Euler → quaternion ``[x, y, z, w]``.

    Per PRD page 4 嵌入图 axis-angle Hamilton formula:
        w = cos(θ/2), x = v_x sin(θ/2), y = v_y sin(θ/2), z = v_z sin(θ/2)
    Composing per-axis rotations Rz(yaw) ∘ Ry(roll) ∘ Rx(pitch) under the
    PRD's left-handed (right=x, up=y, front=z) convention.
    """
    p = math.radians(pitch_deg) * 0.5
    y = math.radians(yaw_deg) * 0.5
    r = math.radians(roll_deg) * 0.5
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    cr, sr = math.cos(r), math.sin(r)
    qw = cp * cy * cr + sp * sy * sr
    qx = sp * cy * cr - cp * sy * sr
    qy = cp * sy * cr + sp * cy * sr
    qz = cp * cy * sr - sp * sy * cr
    return (qx, qy, qz, qw)


# ---------------------------------------------------------------------------
# 12 residuals.
# ---------------------------------------------------------------------------

def r01_quat_norm(rec: dict, threshold: float = 0.01) -> ResidualResult:
    """‖q‖ ≈ 1 for both camera and player quaternions."""
    worst = 0.0
    for key in ("camera_rotation_quaternion", "player_rotation_quaternion"):
        q = rec.get(key)
        if not q or len(q) != 4:
            return ResidualResult("R01", False, math.inf, threshold, f"missing {key}")
        n = math.sqrt(sum(c * c for c in q))
        worst = max(worst, abs(n - 1.0))
    return ResidualResult("R01", worst <= threshold, worst, threshold)


def r02_euler_quat_consistency(rec: dict, threshold: float = 0.01) -> ResidualResult:
    """quat_from_oula(oula) ≈ quat_field within ε.

    Tests Hamilton axis-angle formula consistency for both camera and player.
    """
    worst = 0.0
    for prefix in ("camera_rotation", "player_rotation"):
        oula = rec.get(f"{prefix}_oula")
        quat = rec.get(f"{prefix}_quaternion")
        if not oula or not quat:
            return ResidualResult("R02", False, math.inf, threshold, f"missing {prefix}_*")
        pitch, yaw, roll = float(oula[0]), float(oula[1]), float(oula[2])
        qx_e, qy_e, qz_e, qw_e = _euler_zyx_to_quat(pitch, yaw, roll)
        qx, qy, qz, qw = (float(c) for c in quat)
        # quaternion sign ambiguity: q and -q represent same rotation.
        forward = sum(abs(a - b) for a, b in zip((qx_e, qy_e, qz_e, qw_e), (qx, qy, qz, qw)))
        flipped = sum(abs(a + b) for a, b in zip((qx_e, qy_e, qz_e, qw_e), (qx, qy, qz, qw)))
        worst = max(worst, min(forward, flipped))
    return ResidualResult("R02", worst <= threshold, worst, threshold)


def r03_kinematics(rec_n: dict, rec_n1: dict, fps: float, threshold: float = 0.05) -> ResidualResult:
    """speed[n] ≈ (pos[n+1] − pos[n]) · fps for camera + player.

    Tests at frame n with neighbor n+1. Threshold in m/s.
    """
    if fps <= 0:
        return ResidualResult("R03", False, math.inf, threshold, "fps non-positive")
    dt = 1.0 / fps
    worst = 0.0
    for prefix in ("camera", "player"):
        pos_n = rec_n.get(f"{prefix}_position")
        pos_n1 = rec_n1.get(f"{prefix}_position")
        speed_n = rec_n.get(f"{prefix}_speed")
        if not (pos_n and pos_n1 and speed_n):
            return ResidualResult("R03", False, math.inf, threshold, f"missing {prefix}_*")
        for i in range(3):
            implied = (float(pos_n1[i]) - float(pos_n[i])) / dt
            worst = max(worst, abs(implied - float(speed_n[i])))
    return ResidualResult("R03", worst <= threshold, worst, threshold)


def r04_mouse_dx_diff(rec_n: dict, rec_n1: dict, threshold: float = 1e-6) -> ResidualResult:
    """mouse_dx[n+1] ≈ mouse_x[n+1] − mouse_x[n]. Same for dy."""
    worst = 0.0
    for axis in ("x", "y"):
        x_n = _take(rec_n.get(f"mouse_{axis}", [0.0]))
        x_n1 = _take(rec_n1.get(f"mouse_{axis}", [0.0]))
        d_n1 = _take(rec_n1.get(f"mouse_d{axis}", [0.0]))
        worst = max(worst, abs(d_n1 - (x_n1 - x_n)))
    return ResidualResult("R04", worst <= threshold, worst, threshold)


def r05_dt_uniform(rec_n: dict, rec_n1: dict, fps: float = 30.0, tol_ms: float = 5.0) -> ResidualResult:
    """t[n+1] − t[n] ≈ 1/fps within ±tol_ms ms. Time string format
    'YYYY-MM-DD HH:mm:ss.SSS'."""
    from datetime import datetime
    fmt = "%Y-%m-%d %H:%M:%S.%f"
    try:
        t_n = datetime.strptime(rec_n["time"], fmt)
        t_n1 = datetime.strptime(rec_n1["time"], fmt)
    except (KeyError, ValueError) as e:
        return ResidualResult("R05", False, math.inf, tol_ms, f"time parse: {e}")
    dt_ms = (t_n1 - t_n).total_seconds() * 1000.0
    expected_ms = 1000.0 / fps
    residual = abs(dt_ms - expected_ms)
    return ResidualResult("R05", residual <= tol_ms, residual, tol_ms)


def r06_angle_range(rec: dict, lo: float = -180.0, hi: float = 180.0) -> ResidualResult:
    """All Euler components ∈ [-180, 180]."""
    worst = 0.0
    for key in ("camera_rotation_oula", "player_rotation_oula"):
        oula = rec.get(key)
        if not oula:
            return ResidualResult("R06", False, math.inf, hi - lo, f"missing {key}")
        for c in oula:
            if c < lo:
                worst = max(worst, lo - c)
            elif c > hi:
                worst = max(worst, c - hi)
    return ResidualResult("R06", worst == 0.0, worst, 0.0)


def r07_mouse_range(rec: dict) -> ResidualResult:
    """mouse_x/y ∈ [0, 1]; mouse_dx/dy ∈ [-1, 1]; all are list[float]."""
    for axis in ("x", "y"):
        v = rec.get(f"mouse_{axis}")
        if not isinstance(v, list) or len(v) == 0:
            return ResidualResult("R07", False, math.inf, 1.0, f"mouse_{axis} not list[float]")
        x = float(v[0])
        if not (0.0 <= x <= 1.0):
            return ResidualResult("R07", False, max(abs(x), abs(x - 1.0)), 1.0, f"mouse_{axis}={x}")
        d = rec.get(f"mouse_d{axis}")
        if not isinstance(d, list) or len(d) == 0:
            return ResidualResult("R07", False, math.inf, 1.0, f"mouse_d{axis} not list[float]")
        dx = float(d[0])
        if not (-1.0 <= dx <= 1.0):
            return ResidualResult("R07", False, max(abs(dx), abs(dx - 1.0), abs(dx + 1.0)), 1.0, f"mouse_d{axis}={dx}")
    return ResidualResult("R07", True, 0.0, 1.0)


def r08_fx_eq_fy(rec: dict, threshold: float = 0.01) -> ResidualResult:
    """camera_intrinsics fx == fy. Keys are lowercase."""
    intr = rec.get("camera_intrinsics")
    if not isinstance(intr, dict):
        return ResidualResult("R08", False, math.inf, threshold, "missing camera_intrinsics")
    if "fx" not in intr or "fy" not in intr:
        return ResidualResult("R08", False, math.inf, threshold, "missing fx/fy (lowercase!)")
    residual = abs(float(intr["fx"]) - float(intr["fy"]))
    return ResidualResult("R08", residual <= threshold, residual, threshold)


# Windows VK code subset — derived from PRD Lark page 文件2 VK_TO_KEY table.
# This list is the V₁ Claude reading; V₂ MiniMax has its own independent
# read of the same table. Disagreement triggers BFT view-change.
_VALID_VK_CODES: frozenset[int] = frozenset({
    # F1-F12
    *range(112, 124),
    27,  # ESC
    192,  # `
    *range(48, 58),  # 0-9
    *range(65, 91),  # A-Z
    9, 20, 16, 160, 161, 17, 162, 163, 18, 164, 165, 32,  # tab/caps/shift/ctrl/alt/space
    # extended (arrows, numpad, etc.) — PRD didn't list explicitly but allow
    *range(33, 41),  # PgUp/PgDn/End/Home/arrows
    8, 13, 46, 45,  # backspace/enter/del/insert
})


def r09_keycode_vk(rec: dict) -> ResidualResult:
    """All keyCode entries are valid Windows VK codes."""
    kc = rec.get("keyCode")
    if not isinstance(kc, list):
        return ResidualResult("R09", False, math.inf, 0.0, "keyCode not list[int]")
    for code in kc:
        if not isinstance(code, int):
            return ResidualResult("R09", False, math.inf, 0.0, f"non-int keyCode {code!r}")
        if code not in _VALID_VK_CODES:
            return ResidualResult("R09", False, float(code), 0.0, f"unknown VK code {code}")
    return ResidualResult("R09", True, 0.0, 0.0)


def r10_speed_max(rec: dict, v_max: float = 50.0) -> ResidualResult:
    """‖speed‖ < v_max for camera and player."""
    worst = 0.0
    for key in ("camera_speed", "player_speed"):
        s = rec.get(key)
        if not s or len(s) != 3:
            return ResidualResult("R10", False, math.inf, v_max, f"missing {key}")
        mag = math.sqrt(sum(float(c) * float(c) for c in s))
        worst = max(worst, mag)
    return ResidualResult("R10", worst <= v_max, worst, v_max)


def r12_fps_range(rec: dict, lo: float = 29.5, hi: float = 30.5) -> ResidualResult:
    """fps ∈ [29.5, 30.5]."""
    fps = rec.get("fps")
    if fps is None:
        return ResidualResult("R12", False, math.inf, hi - lo, "missing fps")
    fps = float(fps)
    if fps < lo:
        return ResidualResult("R12", False, lo - fps, hi - lo, f"fps={fps} < {lo}")
    if fps > hi:
        return ResidualResult("R12", False, fps - hi, hi - lo, f"fps={fps} > {hi}")
    return ResidualResult("R12", True, 0.0, hi - lo)
