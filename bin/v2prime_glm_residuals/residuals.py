"""
V2-prime independent verifier — 11 residual checks for action_camera.json
Written by V2 (GLM via Z.AI proxy). References PRD_INPUT.md ONLY.
"""

import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# --- Valid Windows VK codes from PRD ---
VK_TO_KEY = {
    112: 'F1', 113: 'F2', 114: 'F3', 115: 'F4',
    116: 'F5', 117: 'F6', 118: 'F7', 119: 'F8',
    120: 'F9', 121: 'F10', 122: 'F11', 123: 'F12',
    27: 'ESC',
    192: '`',
    48: '0', 49: '1', 50: '2', 51: '3', 52: '4',
    53: '5', 54: '6', 55: '7', 56: '8', 57: '9',
    81: 'Q', 87: 'W', 69: 'E', 82: 'R', 84: 'T',
    89: 'Y', 85: 'U', 73: 'I', 79: 'O', 80: 'P',
    65: 'A', 83: 'S', 68: 'D', 70: 'F', 71: 'G',
    72: 'H', 74: 'J', 75: 'K', 76: 'L',
    90: 'Z', 88: 'X', 67: 'C', 86: 'V', 66: 'B',
    78: 'N', 77: 'M',
    9: 'TAB',
    20: 'CAPS',
    16: 'LSHIFT', 160: 'LSHIFT', 161: 'RSHIFT',
    17: 'LCTRL', 162: 'LCTRL', 163: 'RCTRL',
    18: 'LALT', 164: 'LALT', 165: 'RALT',
    32: 'SPACE',
}
VALID_VK_CODES = set(VK_TO_KEY.keys())


def _get(frame: Dict[str, Any], key: str, default=None):
    """Safe getter that handles keys with spaces."""
    return frame.get(key, default)


# ── r13: keyCode replay from inputs.jsonl ───────────────────────────
def r13_keycode_replay(
    rec: Dict[str, Any],
    neighbor: Optional[Dict[str, Any]] = None,
    inputs_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Replay key_down / key_up events from inputs.jsonl up to the frame
    timestamp and compare the resulting held-keys set with rec["keyCode"].
    """
    import os

    name = "r13_keycode_replay"
    threshold = 0.0

    def _abstain(reason: str) -> Dict[str, Any]:
        return {"name": name, "passed": False, "residual": float("nan"),
                "threshold": threshold, "note": f"ABSTAIN:{reason}"}

    # ── ABSTAIN gates ───────────────────────────────────────────────
    if inputs_path is None:
        return _abstain("no_inputs_path")

    if not os.path.isfile(inputs_path):
        return _abstain("inputs_jsonl_absent")

    # Parse inputs.jsonl
    events: List[Dict[str, Any]] = []
    session_start = None
    with open(inputs_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("event_type") == "session_start":
                session_start = obj
            events.append(obj)

    if session_start is None:
        return _abstain("no_session_start_sentinel")

    fps = session_start.get("fps")
    if fps is None or not (29.5 <= fps <= 30.5):
        return _abstain("fps_out_of_band")

    frame_idx = rec.get("frame")
    if not isinstance(frame_idx, int):
        return _abstain("frame_index_missing")

    # ── Replay ──────────────────────────────────────────────────────
    # Sort events by timestamp
    events.sort(key=lambda e: e.get("timestamp_ms", 0))

    cutoff_ms = (frame_idx + 1) * 1000.0 / fps
    held_keys: set = set()

    for ev in events:
        ts = ev.get("timestamp_ms", 0)
        if ts > cutoff_ms:
            break
        et = ev.get("event_type")
        kc = ev.get("key_code")
        if et == "key_down" and kc is not None:
            held_keys.add(kc)
        elif et == "key_up" and kc is not None:
            held_keys.discard(kc)

    # ── Compare ─────────────────────────────────────────────────────
    actual = set(rec.get("keyCode", []))
    sym_diff = held_keys.symmetric_difference(actual)
    residual = float(len(sym_diff))
    passed = residual == 0.0

    if passed:
        note = "OK"
    else:
        note = (f"keyCode mismatch: replay={sorted(held_keys)} "
                f"vs frame={sorted(actual)}")

    return {"name": name, "passed": passed, "residual": residual,
            "threshold": threshold, "note": note}


# ── r01: quaternion norm ──────────────────────────────────────────────
def r01_quat_norm(frame: Dict[str, Any]) -> Dict[str, Any]:
    """Quaternion [X,Y,Z,W] must have unit norm. Residual = |norm - 1|."""
    threshold = 1e-3
    quat = _get(frame, "camera_rotation_quaternion")
    if quat is None or len(quat) != 4:
        return {"name": "r01_quat_norm", "passed": False, "residual": float("inf"), "threshold": threshold}
    qx, qy, qz, qw = quat
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    residual = abs(norm - 1.0)
    return {"name": "r01_quat_norm", "passed": residual <= threshold, "residual": residual, "threshold": threshold}


# ── r02: euler ↔ quaternion consistency (ZYX intrinsic Hamilton) ──────
def r02_euler_quat_consistency(frame: Dict[str, Any]) -> Dict[str, Any]:
    """
    Euler (camera_rotation_oula) → quaternion via ZYX intrinsic Hamilton.
    Compare with camera_rotation_quaternion [X,Y,Z,W].

    Formula (from prompt):
      Pitch around X, Yaw around Y, Roll around Z.
      cp=cos(pitch_rad/2), sp=sin(pitch_rad/2), etc.
      qw = cp*cy*cr + sp*sy*sr
      qx = sp*cy*cr - cp*sy*sr
      qy = cp*sy*cr + sp*cy*sr
      qz = cp*cy*sr - sp*sy*cr
    """
    threshold = 1e-2
    euler = _get(frame, "camera_rotation_oula")
    quat = _get(frame, "camera_rotation_quaternion")
    if euler is None or quat is None or len(euler) != 3 or len(quat) != 4:
        return {"name": "r02_euler_quat_consistency", "passed": False,
                "residual": float("inf"), "threshold": threshold}

    # euler is [pitch, yaw, roll] in degrees per PRD (Pitch=X idx0, Yaw=Y idx1, Roll=Z idx2)
    pitch_deg, yaw_deg, roll_deg = euler
    pitch_rad = math.radians(pitch_deg)
    yaw_rad = math.radians(yaw_deg)
    roll_rad = math.radians(roll_deg)

    cp = math.cos(pitch_rad / 2.0)
    sp = math.sin(pitch_rad / 2.0)
    cy = math.cos(yaw_rad / 2.0)
    sy = math.sin(yaw_rad / 2.0)
    cr = math.cos(roll_rad / 2.0)
    sr = math.sin(roll_rad / 2.0)

    # ZYX intrinsic Hamilton
    exp_w = cp * cy * cr + sp * sy * sr
    exp_x = sp * cy * cr - cp * sy * sr
    exp_y = cp * sy * cr + sp * cy * sr
    exp_z = cp * cy * sr - sp * sy * cr

    # PRD quaternion order: [X, Y, Z, W]
    act_x, act_y, act_z, act_w = quat

    # quaternion sign ambiguity: q and -q represent same rotation
    diff_pos = math.sqrt(
        (exp_x - act_x) ** 2 + (exp_y - act_y) ** 2 +
        (exp_z - act_z) ** 2 + (exp_w - act_w) ** 2
    )
    diff_neg = math.sqrt(
        (exp_x + act_x) ** 2 + (exp_y + act_y) ** 2 +
        (exp_z + act_z) ** 2 + (exp_w + act_w) ** 2
    )
    residual = min(diff_pos, diff_neg)
    return {"name": "r02_euler_quat_consistency", "passed": residual <= threshold,
            "residual": residual, "threshold": threshold}


# ── r03: kinematics — position delta vs speed*dt ─────────────────────
def r03_kinematics(frame: Dict[str, Any], prev_frame: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    |pos[t] - pos[t-1]| should ≈ |speed| * dt.
    Residual = |distance - speed_mag * dt| / max(distance, speed_mag*dt, 1e-6).
    """
    threshold = 0.5  # relative error tolerance
    if prev_frame is None:
        return {"name": "r03_kinematics", "passed": True, "residual": 0.0, "threshold": threshold}

    pos = _get(frame, "camera_position")
    prev_pos = _get(prev_frame, "camera_position")
    speed = _get(frame, "camera_speed")
    fps = _get(frame, "fps")

    if pos is None or prev_pos is None or speed is None or fps is None or fps <= 0:
        return {"name": "r03_kinematics", "passed": False, "residual": float("inf"), "threshold": threshold}

    dt = 1.0 / fps
    dx = pos[0] - prev_pos[0]
    dy = pos[1] - prev_pos[1]
    dz = pos[2] - prev_pos[2]
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    speed_mag = math.sqrt(speed[0] ** 2 + speed[1] ** 2 + speed[2] ** 2)
    expected = speed_mag * dt
    denom = max(distance, expected, 1e-6)
    residual = abs(distance - expected) / denom
    return {"name": "r03_kinematics", "passed": residual <= threshold, "residual": residual, "threshold": threshold}


# ── r04: mouse dx should be diff of mouse_x between frames ──────────
def r04_mouse_dx_diff(frame: Dict[str, Any], prev_frame: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    mouse_dx[0] should ≈ mouse_x[0](t) - mouse_x[0](t-1) (normalized).
    Residual = |mouse_dx[0] - (mouse_x[0] - prev_mouse_x[0])|.
    """
    threshold = 0.1
    if prev_frame is None:
        return {"name": "r04_mouse_dx_diff", "passed": True, "residual": 0.0, "threshold": threshold}

    mx = _get(frame, "mouse_x")
    prev_mx = _get(prev_frame, "mouse_x")
    mdx = _get(frame, "mouse_dx")

    if mx is None or prev_mx is None or mdx is None:
        return {"name": "r04_mouse_dx_diff", "passed": False, "residual": float("inf"), "threshold": threshold}

    mx_val = mx[0] if isinstance(mx, list) else mx
    prev_mx_val = prev_mx[0] if isinstance(prev_mx, list) else prev_mx
    mdx_val = mdx[0] if isinstance(mdx, list) else mdx

    expected_dx = mx_val - prev_mx_val
    residual = abs(mdx_val - expected_dx)
    return {"name": "r04_mouse_dx_diff", "passed": residual <= threshold, "residual": residual, "threshold": threshold}


# ── r05: dt consistency — 1/fps vs time delta ────────────────────────
def r05_dt(frame: Dict[str, Any], prev_frame: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Time delta between consecutive frames should ≈ 1/fps.
    Residual = |actual_dt - 1/fps|.
    """
    threshold = 0.05  # 50ms tolerance
    if prev_frame is None:
        return {"name": "r05_dt", "passed": True, "residual": 0.0, "threshold": threshold}

    from datetime import datetime

    time_str = _get(frame, "time")
    prev_time_str = _get(prev_frame, "time")
    fps = _get(frame, "fps")

    if time_str is None or prev_time_str is None or fps is None or fps <= 0:
        return {"name": "r05_dt", "passed": False, "residual": float("inf"), "threshold": threshold}

    fmt = "%Y-%m-%d %H:%M:%S.%f"
    try:
        t = datetime.strptime(time_str, fmt)
        pt = datetime.strptime(prev_time_str, fmt)
    except ValueError:
        return {"name": "r05_dt", "passed": False, "residual": float("inf"), "threshold": threshold}

    actual_dt = (t - pt).total_seconds()
    expected_dt = 1.0 / fps
    residual = abs(actual_dt - expected_dt)
    return {"name": "r05_dt", "passed": residual <= threshold, "residual": residual, "threshold": threshold}


# ── r06: euler angles within [-180, 180] ─────────────────────────────
def r06_angle_range(frame: Dict[str, Any]) -> Dict[str, Any]:
    """
    camera_rotation_oula [pitch, yaw, roll] each in [-180, 180].
    Residual = max overshoot beyond range.
    """
    threshold = 0.0
    euler = _get(frame, "camera_rotation_oula")
    if euler is None or len(euler) != 3:
        return {"name": "r06_angle_range", "passed": False, "residual": float("inf"), "threshold": threshold}

    max_overshoot = 0.0
    for angle in euler:
        if angle < -180.0:
            max_overshoot = max(max_overshoot, -180.0 - angle)
        elif angle > 180.0:
            max_overshoot = max(max_overshoot, angle - 180.0)

    return {"name": "r06_angle_range", "passed": max_overshoot <= threshold,
            "residual": max_overshoot, "threshold": threshold}


# ── r07: mouse_x/y in [0,1], mouse_dx/dy in [-1,1] ─────────────────
def r07_mouse_range(frame: Dict[str, Any]) -> Dict[str, Any]:
    """
    mouse_x[0], mouse_y[0] in [0,1]; mouse_dx[0], mouse_dy[0] in [-1,1].
    Residual = max overshoot.
    """
    threshold = 0.0
    mx = _get(frame, "mouse_x")
    my = _get(frame, "mouse_y")
    mdx = _get(frame, "mouse_dx")
    mdy = _get(frame, "mouse_dy")

    if mx is None or my is None or mdx is None or mdy is None:
        return {"name": "r07_mouse_range", "passed": False, "residual": float("inf"), "threshold": threshold}

    mx_val = mx[0] if isinstance(mx, list) else mx
    my_val = my[0] if isinstance(my, list) else my
    mdx_val = mdx[0] if isinstance(mdx, list) else mdx
    mdy_val = mdy[0] if isinstance(mdy, list) else mdy

    overshoot = 0.0
    # mouse_x, mouse_y: [0, 1]
    for v in [mx_val, my_val]:
        if v < 0.0:
            overshoot = max(overshoot, -v)
        elif v > 1.0:
            overshoot = max(overshoot, v - 1.0)
    # mouse_dx, mouse_dy: [-1, 1]
    for v in [mdx_val, mdy_val]:
        if v < -1.0:
            overshoot = max(overshoot, -1.0 - v)
        elif v > 1.0:
            overshoot = max(overshoot, v - 1.0)

    return {"name": "r07_mouse_range", "passed": overshoot <= threshold,
            "residual": overshoot, "threshold": threshold}


# ── r08: fx == fy in camera_intrinsics ───────────────────────────────
def r08_fx_eq_fy(frame: Dict[str, Any]) -> Dict[str, Any]:
    """
    PRD validation #8: camera_intrinsics fx must equal fy.
    Residual = |fx - fy|.
    """
    threshold = 1e-6
    intrinsics = _get(frame, "camera_intrinsics")
    if intrinsics is None:
        return {"name": "r08_fx_eq_fy", "passed": False, "residual": float("inf"), "threshold": threshold}

    fx = intrinsics.get("fx")
    fy = intrinsics.get("fy")
    if fx is None or fy is None:
        return {"name": "r08_fx_eq_fy", "passed": False, "residual": float("inf"), "threshold": threshold}

    residual = abs(fx - fy)
    return {"name": "r08_fx_eq_fy", "passed": residual <= threshold, "residual": residual, "threshold": threshold}


# ── r09: keyCode values must be valid VK codes ──────────────────────
def r09_keycode_vk(frame: Dict[str, Any]) -> Dict[str, Any]:
    """
    All values in keyCode list must be valid Windows VK codes from PRD table.
    Residual = count of invalid codes.
    """
    threshold = 0.0
    keycodes = _get(frame, "keyCode")
    if keycodes is None:
        # keyCode can be absent (no key pressed) — that's valid
        return {"name": "r09_keycode_vk", "passed": True, "residual": 0.0, "threshold": threshold}

    if not isinstance(keycodes, list):
        return {"name": "r09_keycode_vk", "passed": False, "residual": 1.0, "threshold": threshold}

    invalid_count = sum(1 for k in keycodes if k not in VALID_VK_CODES)
    return {"name": "r09_keycode_vk", "passed": invalid_count <= threshold,
            "residual": float(invalid_count), "threshold": threshold}


# ── r10: speed magnitude within physical limits ─────────────────────
def r10_speed_max(frame: Dict[str, Any]) -> Dict[str, Any]:
    """
    camera_speed magnitude should be physically reasonable.
    Typical game character: < 50 m/s. Residual = max(0, |speed| - 50).
    """
    speed_limit = 50.0
    threshold = 0.0
    speed = _get(frame, "camera_speed")
    if speed is None or len(speed) != 3:
        return {"name": "r10_speed_max", "passed": False, "residual": float("inf"), "threshold": threshold}

    mag = math.sqrt(speed[0] ** 2 + speed[1] ** 2 + speed[2] ** 2)
    overshoot = max(0.0, mag - speed_limit)
    return {"name": "r10_speed_max", "passed": overshoot <= threshold,
            "residual": overshoot, "threshold": threshold}


# ── r12: fps within expected range ───────────────────────────────────
def r12_fps_range(frame: Dict[str, Any]) -> Dict[str, Any]:
    """
    PRD says 30 fps recording. fps should be in [20, 60] for sanity.
    Residual = max overshoot beyond [20, 60].
    """
    lo, hi = 20.0, 60.0
    threshold = 0.0
    fps = _get(frame, "fps")
    if fps is None:
        return {"name": "r12_fps_range", "passed": False, "residual": float("inf"), "threshold": threshold}

    overshoot = 0.0
    if fps < lo:
        overshoot = lo - fps
    elif fps > hi:
        overshoot = fps - hi

    return {"name": "r12_fps_range", "passed": overshoot <= threshold,
            "residual": overshoot, "threshold": threshold}


# ── r18: session_id manifest binding (Frankenstein-splice defense) ───
def r18_session_manifest(
    rec: Dict[str, Any],
    neighbor: Optional[Dict[str, Any]] = None,
    manifest_path: Union[str, Path, None] = None,
) -> Dict[str, Any]:
    """V₂' R18: ``rec['session_id']`` must equal ``manifest['session_id']``.

    ABSTAIN encoding: ``passed=False, residual=NaN, note='ABSTAIN:...'``.
    Mirrors V₁ logic — strict equality, four ABSTAIN gates per IL10.
    """
    name = "R18"
    threshold = 0.0

    def _abstain(reason: str) -> Dict[str, Any]:
        return {"name": name, "passed": False, "residual": float("nan"),
                "threshold": threshold, "note": f"ABSTAIN:{reason}"}

    if manifest_path is None:
        return _abstain("no_manifest_path")
    mpath = Path(manifest_path)
    if not mpath.is_file():
        return _abstain("manifest_not_found")

    try:
        manifest: Dict[str, Any] = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return _abstain(f"manifest_unreadable:{e}")

    if "session_id" not in manifest:
        return _abstain("manifest_no_session_id")
    manifest_sid = manifest["session_id"]
    if not isinstance(manifest_sid, str) or manifest_sid == "":
        return _abstain("manifest_session_id_empty")
    if "session_id" not in rec:
        return _abstain("frame_no_session_id")

    frame_sid = rec["session_id"]
    if frame_sid != manifest_sid:
        return {"name": name, "passed": False, "residual": 1.0,
                "threshold": threshold,
                "note": f"session_id mismatch: frame={frame_sid!r} manifest={manifest_sid!r}"}
    return {"name": name, "passed": True, "residual": 0.0,
            "threshold": threshold, "note": "OK"}


# ── r20: dataset-level distribution drift detectors ──────────────────
def _drift_abstain(name: str, reason: str, threshold: float = 0.0) -> Dict[str, Any]:
    return {"name": name, "passed": False, "residual": float("nan"),
            "threshold": threshold, "note": f"ABSTAIN:{reason}"}


def _parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")


def r20a_quat_norm_distribution(
    records: List[Dict[str, Any]],
    max_offset: float = 1e-5,
    max_std: float = 1e-4,
    min_frames: int = 10,
) -> Dict[str, Any]:
    """``|μ_‖q‖ − 1.0| ≤ max_offset`` AND ``σ_‖q‖ ≤ max_std``."""
    name = "R20a"
    if not records:
        return _drift_abstain(name, "empty_records", max_offset)
    if len(records) < min_frames:
        return _drift_abstain(name, f"insufficient_sample({len(records)}<{min_frames})", max_offset)
    norms: List[float] = []
    for r in records:
        q = r.get("camera_rotation_quaternion")
        if not q or len(q) != 4:
            return _drift_abstain(name, "missing_field(camera_rotation_quaternion)", max_offset)
        n = math.sqrt(sum(float(c) * float(c) for c in q))
        if math.isnan(n):
            return _drift_abstain(name, "nan_in_stat", max_offset)
        norms.append(n)
    mu = statistics.fmean(norms)
    sigma = statistics.stdev(norms) if len(norms) > 1 else 0.0
    offset = abs(mu - 1.0)
    passed = offset <= max_offset and sigma <= max_std
    return {"name": name, "passed": passed, "residual": offset,
            "threshold": max_offset,
            "note": f"mu={mu:.3e} sigma={sigma:.3e} sample={len(norms)}"}


def r20b_mouse_dx_cumulative(
    records: List[Dict[str, Any]],
    tolerance: float = 1e-3,
    min_frames: int = 10,
) -> Dict[str, Any]:
    """``|Σ mouse_dx − (mouse_x[N-1] − mouse_x[0])| ≤ tolerance``."""
    name = "R20b"
    if not records:
        return _drift_abstain(name, "empty_records", tolerance)
    if len(records) < min_frames:
        return _drift_abstain(name, f"insufficient_sample({len(records)}<{min_frames})", tolerance)
    s = 0.0
    for r in records:
        dx = r.get("mouse_dx")
        if not isinstance(dx, list) or not dx:
            return _drift_abstain(name, "malformed_field(mouse_dx)", tolerance)
        s += float(dx[0])
    x0 = records[0].get("mouse_x")
    xN = records[-1].get("mouse_x")
    if not isinstance(x0, list) or not x0 or not isinstance(xN, list) or not xN:
        return _drift_abstain(name, "malformed_field(mouse_x)", tolerance)
    delta_x = float(xN[0]) - float(x0[0])
    drift = abs(s - delta_x)
    if math.isnan(drift):
        return _drift_abstain(name, "nan_in_stat", tolerance)
    return {"name": name, "passed": drift <= tolerance, "residual": drift,
            "threshold": tolerance,
            "note": f"sum={s:.3e} delta_x={delta_x:.3e} sample={len(records)}"}


def r20c_fps_jitter(
    records: List[Dict[str, Any]],
    max_offset_ms: float = 0.1,
    max_std_ms: float = 5.0,
    min_frames: int = 10,
) -> Dict[str, Any]:
    """``|μ_dt − 1/fps| ≤ max_offset_ms`` AND ``σ_dt ≤ max_std_ms``."""
    name = "R20c"
    if not records:
        return _drift_abstain(name, "empty_records", max_offset_ms)
    if len(records) < min_frames:
        return _drift_abstain(name, f"insufficient_sample({len(records)}<{min_frames})", max_offset_ms)
    declared_fps = float(records[0].get("fps", 30.0))
    target_dt_ms = 1000.0 / declared_fps if declared_fps > 0 else 1000.0 / 30.0
    dts_ms: List[float] = []
    try:
        for i in range(len(records) - 1):
            dt = (_parse_time(records[i + 1]["time"]) - _parse_time(records[i]["time"])).total_seconds() * 1000.0
            if dt < 0:
                return _drift_abstain(name, "non_monotone_time", max_offset_ms)
            dts_ms.append(dt)
    except (KeyError, ValueError, TypeError):
        return _drift_abstain(name, "malformed_field(time)", max_offset_ms)
    mu = statistics.fmean(dts_ms)
    sigma = statistics.stdev(dts_ms) if len(dts_ms) > 1 else 0.0
    offset = abs(mu - target_dt_ms)
    passed = offset <= max_offset_ms and sigma <= max_std_ms
    return {"name": name, "passed": passed, "residual": offset,
            "threshold": max_offset_ms,
            "note": f"mu_dt={mu:.3f}ms target={target_dt_ms:.3f}ms sigma={sigma:.3f}ms sample={len(dts_ms)}"}


def r20d_speed_profile(
    records: List[Dict[str, Any]],
    max_outlier_pct: float = 0.10,
    max_mean_speed: float = 15.0,
    high_speed_threshold: float = 30.0,
    min_frames: int = 10,
) -> Dict[str, Any]:
    """``ratio(‖speed‖ > 30) ≤ 10%`` AND ``μ_‖speed‖ ≤ 15 m/s``."""
    name = "R20d"
    if not records:
        return _drift_abstain(name, "empty_records", max_outlier_pct)
    if len(records) < min_frames:
        return _drift_abstain(name, f"insufficient_sample({len(records)}<{min_frames})", max_outlier_pct)
    mags: List[float] = []
    for r in records:
        s = r.get("camera_speed")
        if not isinstance(s, list) or len(s) != 3:
            return _drift_abstain(name, "malformed_field(camera_speed)", max_outlier_pct)
        mag = math.sqrt(sum(float(c) * float(c) for c in s))
        if math.isnan(mag):
            return _drift_abstain(name, "nan_in_stat", max_outlier_pct)
        mags.append(mag)
    n_high = sum(1 for m in mags if m > high_speed_threshold)
    ratio = n_high / len(mags)
    mu = statistics.fmean(mags)
    passed = ratio <= max_outlier_pct and mu <= max_mean_speed
    return {"name": name, "passed": passed, "residual": ratio,
            "threshold": max_outlier_pct,
            "note": f"mu_speed={mu:.3f}m/s ratio_high={ratio:.3f} sample={len(mags)}"}


def r20e_yaw_turn_rate(
    records: List[Dict[str, Any]],
    max_rate_deg_per_sec: float = 720.0,
    max_outlier_pct: float = 0.05,
    min_frames: int = 10,
) -> Dict[str, Any]:
    """``ratio(|Δyaw|/dt > 720°/s) ≤ 5%`` over all adjacent pairs."""
    name = "R20e"
    if not records:
        return _drift_abstain(name, "empty_records", max_outlier_pct)
    if len(records) < min_frames:
        return _drift_abstain(name, f"insufficient_sample({len(records)}<{min_frames})", max_outlier_pct)
    rates: List[float] = []
    try:
        for i in range(len(records) - 1):
            oula_n = records[i].get("camera_rotation_oula")
            oula_n1 = records[i + 1].get("camera_rotation_oula")
            if not oula_n or not oula_n1 or len(oula_n) < 2 or len(oula_n1) < 2:
                return _drift_abstain(name, "malformed_field(camera_rotation_oula)", max_outlier_pct)
            d_yaw = float(oula_n1[1]) - float(oula_n[1])
            d_yaw = (d_yaw + 180.0) % 360.0 - 180.0
            dt = (_parse_time(records[i + 1]["time"]) - _parse_time(records[i]["time"])).total_seconds()
            if dt <= 0:
                return _drift_abstain(name, "non_monotone_time", max_outlier_pct)
            rates.append(abs(d_yaw) / dt)
    except (KeyError, ValueError, TypeError):
        return _drift_abstain(name, "malformed_field(time)", max_outlier_pct)
    n_extreme = sum(1 for r in rates if r > max_rate_deg_per_sec)
    ratio = n_extreme / len(rates) if rates else 0.0
    passed = ratio <= max_outlier_pct
    max_rate = max(rates) if rates else 0.0
    return {"name": name, "passed": passed, "residual": ratio,
            "threshold": max_outlier_pct,
            "note": f"ratio_extreme={ratio:.3f} max_rate={max_rate:.1f}deg/s sample={len(rates)}"}


# ── r21: monotonic frame index (D-02 reorder defense) ────────────────
def r21_monotonic_frame(
    rec: Dict[str, Any],
    neighbor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """V₂' R21: rec.frame < neighbor.frame (strict monotonic increase).

    ABSTAIN encoding: ``passed=False, residual=NaN,
    note='ABSTAIN:frame_index_missing'``.
    """
    name = "R21"
    threshold = 0.0
    if neighbor is None:
        return {"name": name, "passed": True, "residual": 0.0,
                "threshold": threshold, "note": "last_frame_no_neighbor"}

    rec_f = rec.get("frame")
    nbr_f = neighbor.get("frame")
    if not isinstance(rec_f, int) or not isinstance(nbr_f, int):
        return {"name": name, "passed": False, "residual": float("nan"),
                "threshold": threshold, "note": "ABSTAIN:frame_index_missing"}

    residual = float(max(0, rec_f - nbr_f + 1))
    if residual == 0.0:
        return {"name": name, "passed": True, "residual": 0.0,
                "threshold": threshold, "note": f"{rec_f} < {nbr_f}"}
    return {"name": name, "passed": False, "residual": residual,
            "threshold": threshold,
            "note": f"out_of_order: rec.frame={rec_f} >= neighbor.frame={nbr_f}"}


# ── Runner ───────────────────────────────────────────────────────────
ALL_SINGLE_FRAME_CHECKS = [
    r01_quat_norm,
    r06_angle_range,
    r07_mouse_range,
    r08_fx_eq_fy,
    r09_keycode_vk,
    r10_speed_max,
    r12_fps_range,
]

ALL_DUAL_FRAME_CHECKS = [
    r02_euler_quat_consistency,  # single-frame but listed here for grouping; actually single
]

ALL_PAIR_CHECKS = [
    r03_kinematics,
    r04_mouse_dx_diff,
    r05_dt,
]


def verify_frame(frame: Dict[str, Any], prev_frame: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Run all 11 residual checks on a single frame."""
    results = []

    # Single-frame checks
    for check_fn in ALL_SINGLE_FRAME_CHECKS:
        results.append(check_fn(frame))

    # Euler-quaternion consistency (single frame)
    results.append(r02_euler_quat_consistency(frame))

    # Pair checks (need prev_frame)
    for check_fn in ALL_PAIR_CHECKS:
        results.append(check_fn(frame, prev_frame))

    return results


def verify_sequence(frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run all checks across a sequence of frames. Returns aggregated results."""
    all_results: Dict[str, List[Dict[str, Any]]] = {}

    for i, frame in enumerate(frames):
        prev = frames[i - 1] if i > 0 else None
        frame_results = verify_frame(frame, prev)
        for r in frame_results:
            name = r["name"]
            if name not in all_results:
                all_results[name] = []
            all_results[name].append(r)

    # Aggregate: a check passes overall only if ALL frames pass
    summary = []
    for name, results_list in all_results.items():
        worst = max(results_list, key=lambda r: r["residual"])
        all_passed = all(r["passed"] for r in results_list)
        fail_count = sum(1 for r in results_list if not r["passed"])
        summary.append({
            "name": name,
            "passed": all_passed,
            "residual": worst["residual"],
            "threshold": worst["threshold"],
            "total_frames": len(results_list),
            "fail_count": fail_count,
        })

    return summary


def main() -> int:
    """CLI entry point for V2-prime residual verifier.

    Args:
        argv: Command-line arguments (sys.argv), expects JSON file path as first arg.

    Returns:
        0 if all checks passed, 1 otherwise.
    """
    if len(sys.argv) < 2:
        print("Usage: python3 v2prime_verifier.py <action_camera.json>")
        print("  Reads JSON (single object or array of frame objects).")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        frames = [data]
    elif isinstance(data, list):
        frames = data
    else:
        print("ERROR: JSON must be a dict (single frame) or list of dicts.")
        sys.exit(1)

    print(f"V2' verifier: {len(frames)} frame(s) loaded.")
    results = verify_sequence(frames)

    passed_all = True
    for r in sorted(results, key=lambda x: x["name"]):
        status = "PASS" if r["passed"] else "FAIL"
        if not r["passed"]:
            passed_all = False
        extra = ""
        if "fail_count" in r:
            extra = f"  ({r['fail_count']}/{r['total_frames']} frames failed)"
        print(f"  [{status}] {r['name']}: residual={r['residual']:.6f} threshold={r['threshold']:.6f}{extra}")

    print()
    if passed_all:
        print("VERDICT: ALL CHECKS PASSED")
    else:
        print("VERDICT: SOME CHECKS FAILED")

    return 0 if passed_all else 1


if __name__ == "__main__":
    main()
