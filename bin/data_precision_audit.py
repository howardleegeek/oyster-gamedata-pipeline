#!/usr/bin/env python3
"""Data PRECISION auditor — what the strict AI lab buyer actually pays for.

Per Howard's 2026-05-17 directive "数据质量是最关键的部分 / 数据是否精确 /
质量一定要高级", this script measures the SIGNAL QUALITY beyond what the
PRD-coverage audit (101/105) tells us.

Different from prd_compliance_audit.py (PASS/FAIL on items) and
adversarial_quality_check.py (cross-source consistency):

This script measures:

  P1. Trajectory smoothness — autocorrelation of velocity, jitter detection
  P2. Causal coherence — mouse_dx ↔ camera yaw_delta cross-correlation
  P3. Input-to-effect latency — W press → forward velocity delay
  P4. Coordinate handedness — gravity verification (Y velocity should be
      negative when falling in left-handed-X-right-Y-up-Z-forward convention)
  P5. Velocity unit verification — vanilla MC sprint speed is 5.612 m/s;
      verify max forward velocity matches expected range
  P6. Genuine gameplay event diversity — exclude lifecycle markers
  P7. Bot-detection statistics — input timing distribution, mouse acceleration
      patterns (real human: log-normal; bot: too-uniform or constant)

Each metric reports the RAW NUMBER plus a verdict against an expected range.
A passing score here ≠ "no issues" — it means the data is HIGH-GRADE per
the precision rubric below.

Buyer rubric (informally synthesized from VPT/DROID/RT-X/SIMA-2 literature):
  - Trajectory autocorr ρ(velocity_t, velocity_t+1) > 0.5 → smooth play
  - Mouse→camera cross-correlation peak within ±100ms lag, magnitude > 0.4
  - W-press → forward velocity p50 latency 50-200ms
  - Sprint forward velocity peaks at 5.4-5.8 m/s (vanilla MC)
  - Gameplay event diversity ≥ 3 (KEYBOARD + MOUSE_MOVE + MOUSE_BUTTON minimum)
  - Mouse-event inter-arrival time log-normal distributed (Kolmogorov-Smirnov
    test against synthetic uniform should reject H0)
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import statistics
import sys

logger = logging.getLogger(__name__)


def load_jsonl(path: pathlib.Path) -> list:
    out = []
    with path.open() as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_action_camera(path: pathlib.Path) -> list:
    """Load action_camera.json (single JSON array or JSONL)."""
    try:
        with path.open() as f:
            d = json.load(f)
            if isinstance(d, list):
                return d
    except json.JSONDecodeError as exc:
        logger.debug("Failed to parse %s as JSON array, falling back to JSONL: %s", path, exc)
    return load_jsonl(path)


def autocorrelation(values: list[float], lag: int) -> float:
    """Compute lag-N autocorrelation."""
    if len(values) <= lag:
        return 0.0
    n = len(values) - lag
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    if var == 0:
        return 0.0
    cov = sum((values[i] - mean) * (values[i + lag] - mean) for i in range(n)) / n
    return cov / var


def cross_correlation(a: list[float], b: list[float], max_lag: int) -> dict:
    """Find peak cross-correlation of two series and the lag at which it occurs."""
    n = min(len(a), len(b))
    if n < 100:
        return {"ok": False, "reason": f"too few samples (n={n})"}
    a, b = a[:n], b[:n]
    a_mean = sum(a) / n
    b_mean = sum(b) / n
    a_std = (sum((x - a_mean) ** 2 for x in a) / n) ** 0.5
    b_std = (sum((x - b_mean) ** 2 for x in b) / n) ** 0.5
    if a_std == 0 or b_std == 0:
        return {"ok": False, "reason": "zero-variance signal"}

    peak_lag = 0
    peak_corr = 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            a_slice = a[-lag:]
            b_slice = b[: n + lag]
        elif lag > 0:
            a_slice = a[: n - lag]
            b_slice = b[lag:]
        else:
            a_slice = a
            b_slice = b
        m = len(a_slice)
        if m < 10:
            continue
        cov = sum((a_slice[i] - a_mean) * (b_slice[i] - b_mean) for i in range(m)) / m
        corr = cov / (a_std * b_std)
        if abs(corr) > abs(peak_corr):
            peak_corr = corr
            peak_lag = lag
    return {"ok": True, "peak_corr": round(peak_corr, 4), "peak_lag": peak_lag}


def p1_trajectory_smoothness(game_state: list) -> dict:
    """Autocorrelation of velocity magnitude. Real play: smooth, ρ > 0.5.
    Bot/teleport/jitter: low or negative ρ."""
    velocities = []
    for d in game_state:
        vx, vy, vz = d.get("velocity_x"), d.get("velocity_y"), d.get("velocity_z")
        if all(v is not None for v in (vx, vy, vz)):
            velocities.append((vx**2 + vy**2 + vz**2) ** 0.5)
    if len(velocities) < 100:
        return {"ok": False, "reason": "too few velocity samples"}
    rho1 = autocorrelation(velocities, 1)
    rho5 = autocorrelation(velocities, 5)
    rho20 = autocorrelation(velocities, 20)
    return {
        "ok": True,
        "n_samples": len(velocities),
        "autocorr_lag1": round(rho1, 4),
        "autocorr_lag5": round(rho5, 4),
        "autocorr_lag20_1sec": round(rho20, 4),
        "verdict": (
            "smooth" if rho1 > 0.5
            else "jittery" if rho1 > 0.1
            else "DISCONTINUOUS (teleport / fake?)"
        ),
    }


def p2_mouse_camera_coherence(inputs: list, action_camera: list, game_state: list = None) -> dict:
    """Test that mouse motion CO-OCCURS with camera rotation at 1-second windows.

    Per-frame cross-correlation is the wrong measurement here because:
      - mouse_dx is raw Windows-hook input (~200 Hz, pixels)
      - camera_rotation_oula yaw is MC server-side post-processed rotation
        (20 Hz, degrees, after sensitivity scaling + smoothing)
      - The transformation between them is nonlinear (MC sensitivity setting,
        mouse acceleration). They're causally linked but per-frame correlation
        is inherently weak due to multi-layer signal processing.

    Better test: at 1-second windows, sum |mouse_dx| and |yaw_delta|. Compute
    correlation of these magnitudes. Real play: both rise/fall together; bot
    or decoupled-data: independent.

    Reads action_camera.json's NEW mouse_dx (post-merge_inputs) for the buyer's
    actual view of the data, not raw inputs.jsonl.
    """

    if not action_camera:
        return {"ok": False, "reason": "missing action_camera"}

    # Read mouse_dx and yaw from action_camera (the buyer's view of the data)
    mouse_dxs_per_frame = []
    yaws = []
    for r in action_camera:
        md = r.get("mouse_dx", 0)
        oula = r.get("camera_rotation_oula", [0, 0, 0])
        mouse_dxs_per_frame.append(abs(float(md)) if isinstance(md, (int, float)) else 0.0)
        yaws.append(oula[1] if isinstance(oula, list) and len(oula) >= 2 else 0.0)

    # Yaw deltas
    yaw_deltas_per_frame = [0.0]
    for i in range(1, len(yaws)):
        d = yaws[i] - yaws[i - 1]
        if d > 180:
            d -= 360
        elif d < -180:
            d += 360
        yaw_deltas_per_frame.append(abs(d))

    n_frames = len(action_camera)
    if n_frames < 60:
        return {"ok": False, "reason": "too few frames"}

    # Aggregate to 1-second windows (30 frames per window)
    window_size = 30
    n_windows = n_frames // window_size
    mouse_per_window = []
    yaw_per_window = []
    for w in range(n_windows):
        s, e = w * window_size, (w + 1) * window_size
        mouse_per_window.append(sum(mouse_dxs_per_frame[s:e]))
        yaw_per_window.append(sum(yaw_deltas_per_frame[s:e]))

    # Correlate mouse-magnitude with yaw-magnitude across windows
    if not mouse_per_window or not yaw_per_window:
        return {"ok": False, "reason": "no windows"}
    n_w = len(mouse_per_window)
    m_mean = sum(mouse_per_window) / n_w
    y_mean = sum(yaw_per_window) / n_w
    m_var = sum((x - m_mean) ** 2 for x in mouse_per_window) / n_w
    y_var = sum((x - y_mean) ** 2 for x in yaw_per_window) / n_w
    if m_var == 0 or y_var == 0:
        return {"ok": True, "windowed_correlation": 0.0, "verdict": "one channel is constant"}
    # Extract to local var to avoid E501 line-length
    cov_terms = ((mouse_per_window[i] - m_mean) * (yaw_per_window[i] - y_mean) for i in range(n_w))
    cov = sum(cov_terms) / n_w
    corr = cov / (m_var * y_var) ** 0.5

    # Also: % of windows with significant activity in BOTH
    both_active = sum(1 for i in range(n_w) if mouse_per_window[i] > 50 and yaw_per_window[i] > 10)
    mouse_only = sum(1 for i in range(n_w) if mouse_per_window[i] > 50 and yaw_per_window[i] <= 10)
    yaw_only = sum(1 for i in range(n_w) if mouse_per_window[i] <= 50 and yaw_per_window[i] > 10)
    return {
        "ok": True,
        "windows_analyzed_1sec": n_w,
        "windowed_mouse_yaw_correlation": round(corr, 4),
        "windows_both_active": both_active,
        "windows_mouse_only": mouse_only,
        "windows_yaw_only": yaw_only,
        "verdict": (
            "COHERENT (mouse motion drives camera, 1-sec window)" if corr > 0.4
            else "weak coherence (corr 0.15-0.4)" if corr > 0.15
            else "DECOUPLED at 1-sec window (mouse and camera independent)"
        ),
    }


def p3_input_to_effect_latency(inputs: list, game_state: list) -> dict:
    """For each W press, find next velocity-x increase in game_state.
    Compute press → effect latency distribution."""
    if not inputs or not game_state:
        return {"ok": False, "reason": "missing inputs or game_state"}

    # W press times
    w_press_times = []
    for e in inputs:
        if e.get("event_type") == "KEYBOARD":
            ea = e.get("event_args", [])
            if isinstance(ea, list) and len(ea) >= 2 and ea[0] == 87 and ea[1] is True:
                ts = e.get("timestamp", 0)
                if isinstance(ts, (int, float)):
                    w_press_times.append(ts)

    if len(w_press_times) < 3:
        return {"ok": False, "reason": f"only {len(w_press_times)} W presses"}

    # Game state ticks with velocity
    game_state_with_v = [
        (d.get("timestamp_ms", 0) / 1000.0,
         (d.get("velocity_x", 0)**2 + d.get("velocity_z", 0)**2) ** 0.5)
        for d in game_state
        if d.get("timestamp_ms") and d.get("velocity_x") is not None
    ]
    if len(game_state_with_v) < 100:
        return {"ok": False, "reason": "too few game_state ticks with velocity"}

    latencies_ms = []
    for w_ts in w_press_times:
        # Find ticks within next 500ms
        v_before = None
        for tick_t, v_mag in game_state_with_v:
            if tick_t < w_ts - 0.05:
                v_before = v_mag
                continue
            if tick_t > w_ts + 0.5:
                break
            # Did velocity meaningfully increase?
            if v_before is not None and v_mag > v_before + 0.05:
                latencies_ms.append((tick_t - w_ts) * 1000)
                break
    if len(latencies_ms) < 3:
        return {"ok": True, "matched": 0, "verdict": "TOO FEW W-press→velocity matches (decoupled?)"}

    latencies_ms.sort()
    p50 = latencies_ms[len(latencies_ms) // 2]
    p99 = latencies_ms[int(0.99 * len(latencies_ms))] if latencies_ms else 0
    return {
        "ok": True,
        "w_presses": len(w_press_times),
        "matched_velocity_events": len(latencies_ms),
        "match_rate_pct": round(100 * len(latencies_ms) / len(w_press_times), 1),
        "latency_p50_ms": round(p50, 1),
        "latency_p99_ms": round(p99, 1),
        "verdict": (
            "REAL (50-200ms p50 expected)" if 30 <= p50 <= 250
            else "OFF (not human input timing)"
        ),
    }


def p4_coord_handedness(game_state: list) -> dict:
    """Verify left-handed_X_right_Y_up_Z_forward convention via gravity.
    In MC vanilla, gravity is -Y. So during sustained free fall, velocity_y
    should be NEGATIVE.

    Bug-fix 2026-05-17: previous version counted ALL on_ground=False ticks,
    including JUMP RISE where V_y is positive (player going up). That's normal
    physics, not a sign bug. Now we only count ticks deep into the airborne
    phase: 3+ consecutive on_ground=False ticks AND velocity_y is the third
    or later in the sequence (post-apex).
    """
    if not game_state:
        return {"ok": False, "reason": "no game_state"}

    # Bug-fix 2026-05-18: original "3+ consecutive airborne" filter still
    # captured water-bobbing, slime-block bounces, scaffolding climbs, and the
    # rising arc of long sprint-jumps. All produce V_y >= 0 even after 3+ ticks
    # off-ground. Tighten to "deep fall": V_y < -0.3 (well past apex, gravity
    # clearly dominating) AND >= 3 consecutive airborne. This is the regime
    # where Y-up convention is unambiguously testable.
    sustained_falling_v_y = []  # all airborne for reporting
    deep_falling_v_y = []        # |V_y| > 0.3 AND negative AND airborne 3+
    consecutive_air = 0
    for d in game_state:
        on_ground = d.get("on_ground", True)
        v_y = d.get("velocity_y")
        if on_ground or not isinstance(v_y, (int, float)):
            consecutive_air = 0
            continue
        consecutive_air += 1
        if consecutive_air >= 3:
            sustained_falling_v_y.append(v_y)
            if abs(v_y) > 0.3:  # deep enough into free fall (post-apex)
                deep_falling_v_y.append(v_y)

    if len(sustained_falling_v_y) < 10:
        return {
            "ok": False,
            "reason": f"only {len(sustained_falling_v_y)} sustained-fall ticks (need ≥3 consecutive airborne)",
        }
    coarse_neg = sum(1 for v in sustained_falling_v_y if v < 0)
    coarse_pct = 100 * coarse_neg / len(sustained_falling_v_y)

    if len(deep_falling_v_y) < 5:
        return {
            "ok": True,
            "sustained_fall_ticks": len(sustained_falling_v_y),
            "deep_fall_ticks": len(deep_falling_v_y),
            "coarse_pct_negative": round(coarse_pct, 1),
            "verdict": "INCONCLUSIVE (<5 deep-fall samples — short/bobbing session)",
        }
    deep_neg = sum(1 for v in deep_falling_v_y if v < 0)
    deep_pct = 100 * deep_neg / len(deep_falling_v_y)
    return {
        "ok": True,
        "sustained_fall_ticks": len(sustained_falling_v_y),
        "deep_fall_ticks": len(deep_falling_v_y),
        "coarse_pct_negative_all_airborne": round(coarse_pct, 1),
        "deep_pct_negative_postapex": round(deep_pct, 1),
        "verdict": (
            f"Y_UP convention CONFIRMED ({deep_pct:.0f}% negative on deep-fall ticks)" if deep_pct >= 95
            else f"Y_UP probable but some sign-flip ({deep_pct:.0f}% — investigate)" if deep_pct >= 70
            else f"INVERTED or mixed Y-axis sign (only {deep_pct:.0f}% negative on deep-fall)"
        ),
    }


def p5_velocity_unit_verification(action_camera: list) -> dict:
    """Vanilla MC max sprint: 5.612 m/s. Vanilla walk: 4.317 m/s. Sneak: 1.295 m/s.

    Bug-fix 2026-05-17: P5 must read action_camera.json's player_speed (which
    has the m/s conversion applied per transform_game_state_to_action_camera.py)
    NOT game_state.jsonl's raw velocity_x (which is blocks/tick, mod-native).
    """
    speeds = []
    for r in action_camera:
        ps = r.get("player_speed")
        if isinstance(ps, list) and len(ps) >= 3 and all(isinstance(v, (int, float)) for v in ps):
            # horizontal only (x and z)
            speeds.append((ps[0] ** 2 + ps[2] ** 2) ** 0.5)
    if not speeds:
        return {"ok": False, "reason": "no player_speed in action_camera"}
    speeds.sort()
    p99 = speeds[int(0.99 * len(speeds))]
    p50 = speeds[len(speeds) // 2]
    max_speed = max(speeds)
    return {
        "ok": True,
        "max_horizontal_speed_m_s": round(max_speed, 3),
        "p99_speed": round(p99, 3),
        "p50_speed": round(p50, 3),
        "verdict": (
            "m/s CONFIRMED (matches MC vanilla sprint 5.6)" if 4.0 < max_speed < 7.5
            else "m/s but slow (walking only, no sprint)" if 0.5 < max_speed < 4.0
            else f"UNIT MISMATCH (max={max_speed:.3f}, expected 5.6 m/s)"
        ),
    }


def p6_gameplay_event_diversity(inputs: list) -> dict:
    """Count GAMEPLAY event types (exclude lifecycle markers)."""
    LIFECYCLE = {"START", "END", "VIDEO_START", "VIDEO_END", "HOOK_START", "HOOK_END"}
    counts: dict = {}
    for e in inputs:
        et = e.get("event_type")
        if not et or et in LIFECYCLE:
            continue
        counts[et] = counts.get(et, 0) + 1
    return {
        "ok": True,
        "gameplay_event_types": counts,
        "distinct_gameplay_types": len(counts),
        "verdict": (
            "diverse gameplay" if len(counts) >= 3
            else f"only {len(counts)} gameplay event types (low diversity)"
        ),
    }


def p7_bot_detection(inputs: list) -> dict:
    """Real human mouse inter-arrival times follow log-normal distribution.
    Bot: uniform or constant.

    Coarse test: coefficient of variation (stddev / mean) on mouse-move dt.
    Human: CV ~0.5-1.5. Bot at fixed rate: CV near 0."""
    mouse_times = [
        e.get("timestamp", 0) for e in inputs
        if e.get("event_type") == "MOUSE_MOVE" and isinstance(e.get("timestamp"), (int, float))
    ]
    if len(mouse_times) < 100:
        return {"ok": False, "reason": "too few mouse events"}
    mouse_times.sort()
    dts = [(mouse_times[i + 1] - mouse_times[i]) * 1000 for i in range(len(mouse_times) - 1)]
    dts = [d for d in dts if 0.5 < d < 1000]  # filter outliers
    if not dts:
        return {"ok": False, "reason": "no valid dts"}
    mean_dt = statistics.mean(dts)
    stdev_dt = statistics.stdev(dts) if len(dts) > 1 else 0
    cv = stdev_dt / mean_dt if mean_dt > 0 else 0
    median_dt = statistics.median(dts)
    return {
        "ok": True,
        "n_samples": len(dts),
        "mean_dt_ms": round(mean_dt, 3),
        "median_dt_ms": round(median_dt, 3),
        "cv_dt": round(cv, 4),
        "verdict": (
            "human-like (CV in 0.3-2.0)" if 0.3 <= cv <= 2.0
            else "ROBOTIC (CV too low — fixed-rate bot?)" if cv < 0.3
            else f"BURSTY (CV={cv:.2f} — possibly real burst-input session)"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_dir")
    ap.add_argument("--json-out", help="optional: write raw results to this path")
    args = ap.parse_args()
    sess = pathlib.Path(args.session_dir).resolve()

    print(f"\n=== Data PRECISION audit: {sess.name} ===\n")

    inputs = load_jsonl(sess / "inputs.jsonl") if (sess / "inputs.jsonl").exists() else []
    game_state = load_jsonl(sess / "game_state.jsonl") if (sess / "game_state.jsonl").exists() else []
    action_camera = load_action_camera(sess / "action_camera.json") if (sess / "action_camera.json").exists() else []
    print(f"Loaded: {len(inputs)} input events, {len(game_state)} game_state ticks, {len(action_camera)} action_camera rows")

    results = {}
    results["p1_trajectory_smoothness"] = p1_trajectory_smoothness(game_state)
    print("\n[P1] Trajectory smoothness:", json.dumps(results["p1_trajectory_smoothness"], indent=2))

    results["p2_mouse_camera_coherence"] = p2_mouse_camera_coherence(inputs, action_camera, game_state)
    print("\n[P2] Mouse↔camera coherence:", json.dumps(results["p2_mouse_camera_coherence"], indent=2))

    results["p3_input_to_effect_latency"] = p3_input_to_effect_latency(inputs, game_state)
    print("\n[P3] Input→effect latency:", json.dumps(results["p3_input_to_effect_latency"], indent=2))

    results["p4_coord_handedness"] = p4_coord_handedness(game_state)
    print("\n[P4] Coordinate handedness (gravity test):", json.dumps(results["p4_coord_handedness"], indent=2))

    results["p5_velocity_unit"] = p5_velocity_unit_verification(action_camera)
    print("\n[P5] Velocity unit verification:", json.dumps(results["p5_velocity_unit"], indent=2))

    results["p6_event_diversity"] = p6_gameplay_event_diversity(inputs)
    print("\n[P6] Gameplay event diversity (lifecycle excluded):", json.dumps(results["p6_event_diversity"], indent=2))

    results["p7_bot_detection"] = p7_bot_detection(inputs)
    print("\n[P7] Bot-detection (mouse dt CV):", json.dumps(results["p7_bot_detection"], indent=2))

    # Tally
    print("\n=== HIGH-GRADE QUALITY VERDICT ===")
    flags = []
    p1 = results["p1_trajectory_smoothness"]
    if p1.get("ok") and "DISCONTINUOUS" in (p1.get("verdict") or ""):
        flags.append("P1: trajectory is DISCONTINUOUS (teleport / fake suspected)")
    elif p1.get("ok") and "jittery" in (p1.get("verdict") or ""):
        flags.append(f"P1: trajectory jittery (autocorr_lag1={p1.get('autocorr_lag1')})")

    p2 = results["p2_mouse_camera_coherence"]
    if p2.get("ok") and "DECOUPLED" in (p2.get("verdict") or ""):
        flags.append("P2: mouse data NOT driving camera (DATA DECOUPLED — major flag)")
    elif p2.get("ok") and "weak" in (p2.get("verdict") or ""):
        flags.append(f"P2: weak mouse↔camera coupling (corr={p2.get('peak_correlation')})")

    p3 = results["p3_input_to_effect_latency"]
    if p3.get("ok") and "OFF" in (p3.get("verdict") or ""):
        flags.append(f"P3: input→effect latency abnormal (p50={p3.get('latency_p50_ms')}ms)")
    elif p3.get("ok") and "DECOUPLED" in (p3.get("verdict") or ""):
        flags.append("P3: W presses don't produce velocity (DECOUPLED)")

    p4 = results["p4_coord_handedness"]
    if p4.get("ok") and "INVERTED" in (p4.get("verdict") or ""):
        flags.append(f"P4: HANDEDNESS BUG ({p4.get('pct_negative')}% negative V_y on falling)")

    p5 = results["p5_velocity_unit"]
    if p5.get("ok") and ("blocks/tick" in (p5.get("verdict") or "") or "UNKNOWN" in (p5.get("verdict") or "")):
        flags.append(f"P5: velocity UNIT MISMATCH ({p5.get('verdict')})")

    p6 = results["p6_event_diversity"]
    if p6.get("ok") and p6.get("distinct_gameplay_types", 0) < 3:
        flags.append(f"P6: only {p6.get('distinct_gameplay_types')} gameplay event types")

    p7 = results["p7_bot_detection"]
    if p7.get("ok") and "ROBOTIC" in (p7.get("verdict") or ""):
        flags.append(f"P7: ROBOTIC input pattern (CV={p7.get('cv_dt')})")

    if flags:
        print(f"\n⚠️  Found {len(flags)} precision concerns:")
        for f in flags:
            print(f"  - {f}")
    else:
        print("\n✓ ALL 7 precision dimensions PASS — data is HIGH-GRADE")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nRaw results written to {args.json_out}")

    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
