"""V₂' (GLM) BFT GREEN coverage for R18, R20a-e, R21.

Mirrors V₁ test cases. V₂' returns dict shape (not ResidualResult);
ABSTAIN encoded as passed=False, residual=NaN, note prefix 'ABSTAIN:'.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

from bin.v2prime_glm_residuals import (
    r18_session_manifest,
    r20a_quat_norm_distribution, r20b_mouse_dx_cumulative,
    r20c_fps_jitter, r20d_speed_profile, r20e_yaw_turn_rate,
    r21_monotonic_frame,
)


def _ts(i: int, fps: float = 30.0) -> str:
    """Generate evenly-spaced timestamp for frame i at given fps."""
    sec = i / fps
    base_s = int(sec)
    micro = int((sec - base_s) * 1_000_000)
    return f"2026-01-01 00:00:{base_s:02d}.{micro:06d}"


def _honest_records(n: int = 100) -> list[dict]:
    """N frames: unit quaternion, near-zero mouse, 30fps, low speed, low yaw rate."""
    return [{
        "frame": i,
        "time": _ts(i),
        "fps": 30.0,
        "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
        "camera_rotation_oula": [0.0, i * 0.1, 0.0],
        "camera_position": [i * 0.01, 0.0, 0.0],
        "camera_speed": [1.0, 0.0, 0.0],
        "mouse_x": [0.5 + i * 0.0001], "mouse_y": [0.5],
        "mouse_dx": [0.0001], "mouse_dy": [0.0],
    } for i in range(n)]


# ── R18 ────────────────────────────────────────────────────────────
def test_r18_match_passes():
    sid = "uuid-1234"
    with tempfile.TemporaryDirectory() as tmp:
        mp = Path(tmp) / "manifest.json"
        mp.write_text(json.dumps({"session_id": sid}))
        r = r18_session_manifest({"frame": 0, "session_id": sid}, manifest_path=mp)
    assert r["passed"] is True and r["residual"] == 0.0


def test_r18_mismatch_fails():
    with tempfile.TemporaryDirectory() as tmp:
        mp = Path(tmp) / "manifest.json"
        mp.write_text(json.dumps({"session_id": "A"}))
        r = r18_session_manifest({"session_id": "B"}, manifest_path=mp)
    assert r["passed"] is False and r["residual"] == 1.0
    assert "mismatch" in r["note"]


def test_r18_no_manifest_abstains():
    r = r18_session_manifest({"session_id": "A"}, manifest_path=None)
    assert r["passed"] is False and math.isnan(r["residual"])
    assert r["note"].startswith("ABSTAIN:")


# ── R20a ───────────────────────────────────────────────────────────
def test_r20a_honest_passes():
    r = r20a_quat_norm_distribution(_honest_records(100))
    assert r["passed"] is True


def test_r20a_drift_fails():
    recs = _honest_records(100)
    for i, rec in enumerate(recs):
        # drift quaternion norm slightly above 1.0
        rec["camera_rotation_quaternion"] = [0.0, 0.0, 0.0, 1.0 + (i % 7) * 0.01]
    r = r20a_quat_norm_distribution(recs)
    assert r["passed"] is False


# ── R20b ───────────────────────────────────────────────────────────
def test_r20b_drift_fails():
    recs = _honest_records(100)
    # break cumulative invariant: mouse_dx all zero but mouse_x grows
    for rec in recs:
        rec["mouse_dx"] = [0.0]
    recs[-1]["mouse_x"] = [0.999]
    r = r20b_mouse_dx_cumulative(recs)
    assert r["passed"] is False and r["residual"] > 1e-3


# ── R20c ───────────────────────────────────────────────────────────
def test_r20c_jitter_fails():
    recs = _honest_records(100)
    # alternate dt = 10ms / 56ms (mean ~33ms target, std huge >5ms)
    base_us = 0
    for i, rec in enumerate(recs):
        base_us += 10_000 if i % 2 == 0 else 56_000
        sec = base_us / 1_000_000
        s_int = int(sec)
        micro = int((sec - s_int) * 1_000_000)
        rec["time"] = f"2026-01-01 00:00:{s_int:02d}.{micro:06d}"
    r = r20c_fps_jitter(recs)
    assert r["passed"] is False


# ── R20d ───────────────────────────────────────────────────────────
def test_r20d_speed_outliers_fail():
    recs = _honest_records(100)
    # 20% of frames at 50 m/s — exceeds high_speed_threshold=30
    for i in range(0, 100, 5):
        recs[i]["camera_speed"] = [50.0, 0.0, 0.0]
    r = r20d_speed_profile(recs)
    assert r["passed"] is False


# ── R20e ───────────────────────────────────────────────────────────
def test_r20e_yaw_turn_rate_fails():
    recs = _honest_records(100)
    # yaw flips by 90° every frame at 30fps -> 2700°/s, exceeds 720°/s
    for i, rec in enumerate(recs):
        rec["camera_rotation_oula"] = [0.0, (i * 90.0) % 360.0, 0.0]
    r = r20e_yaw_turn_rate(recs)
    assert r["passed"] is False


# ── R21 ────────────────────────────────────────────────────────────
def test_r21_monotonic_passes():
    r = r21_monotonic_frame({"frame": 0}, {"frame": 1})
    assert r["passed"] is True and r["residual"] == 0.0


def test_r21_out_of_order_fails():
    r = r21_monotonic_frame({"frame": 5}, {"frame": 4})
    assert r["passed"] is False and r["residual"] == 2.0


def test_r21_duplicate_fails():
    r = r21_monotonic_frame({"frame": 5}, {"frame": 5})
    assert r["passed"] is False and r["residual"] == 1.0


def test_r21_no_neighbor_passes():
    r = r21_monotonic_frame({"frame": 9000}, None)
    assert r["passed"] is True and r["residual"] == 0.0
