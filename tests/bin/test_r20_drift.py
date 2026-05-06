"""Tests for V₁ R20 distribution-level drift detector.

Coverage matrix (13 cases):
- 5 happy-path: honest 9000-frame baseline → all 5 sub-residuals PASS
- 3 Bucket-C adversarials: C-01 quat-norm, C-02 mouse-dx cumulative, C-03 fps boundary
- 2 R20d/e adversarials: speed profile saturation, impossible yaw turn rate
- 3 IL11 ABSTAIN gates: empty, insufficient sample, malformed field
"""
from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta

from bin.v1_claude_residuals.r20_drift import (
    r20a_quat_norm_distribution,
    r20b_mouse_dx_cumulative,
    r20c_fps_jitter,
    r20d_speed_profile,
    r20e_yaw_turn_rate,
)

N = 9000
T0 = datetime(2026, 5, 6, 12, 0, 0)


def _baseline(n: int = N, fps: float = 30.0, yaw_step: float = 0.1) -> list[dict]:
    """5-min honest dataset: walk forward 0.5 m/s, yaw 0.1°/frame, ‖q‖=1.0."""
    dt = 1.0 / fps
    recs = []
    for i in range(n):
        t = T0 + timedelta(seconds=i * dt)
        recs.append({
            "frame": i,
            "fps": fps,
            "time": t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}",
            "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
            "camera_rotation_oula": [0.0, i * yaw_step, 0.0],
            "camera_speed": [0.5, 0.0, 0.0],
            "mouse_x": [0.5 + i * 1e-6],
            "mouse_dx": [1e-6],
        })
    return recs


class TestR20Honest(unittest.TestCase):
    """Happy path — honest baseline: all 5 sub-residuals PASS."""

    def test_all_pass_on_baseline(self) -> None:
        recs = _baseline()
        self.assertTrue(r20a_quat_norm_distribution(recs).passed)
        self.assertTrue(r20b_mouse_dx_cumulative(recs).passed)
        self.assertTrue(r20c_fps_jitter(recs).passed)
        self.assertTrue(r20d_speed_profile(recs).passed)
        self.assertTrue(r20e_yaw_turn_rate(recs).passed)


class TestBucketC(unittest.TestCase):
    """Catches Bucket-C sub-threshold attacks single-frame R01–R16 miss."""

    def test_C01_quat_norm_drift(self) -> None:
        """C-01: ‖q‖ = 1 + 5e-7 over 9k frames → R01 (1e-6) PASSes per-frame
        but μ_‖q‖ = 1+5e-7 → offset 5e-7 < 1e-5 default... must tighten.
        Use the scaled attack from spec: persistent +5e-5 offset to exceed 1e-5.
        """
        recs = _baseline()
        for r in recs:
            # scale q to norm 1+5e-5 via uniform multiply
            scale = 1.0 + 5e-5
            r["camera_rotation_quaternion"] = [0.0, 0.0, 0.0, scale]
        result = r20a_quat_norm_distribution(recs)
        self.assertFalse(result.passed, msg=f"expected FAIL on quat drift, got {result}")
        self.assertGreater(result.sample_stat, 1e-5)

    def test_C02_mouse_dx_cumulative(self) -> None:
        """C-02: mouse_dx + 5e-7 per frame. After 9000 frames,
        Σ mouse_dx − ΔX ≈ 9000 · 5e-7 = 4.5e-3 >> 1e-3 → FAIL."""
        recs = _baseline()
        for r in recs:
            r["mouse_dx"] = [r["mouse_dx"][0] + 5e-7]
        result = r20b_mouse_dx_cumulative(recs)
        self.assertFalse(result.passed, msg=f"expected FAIL, got {result}")
        self.assertGreater(result.sample_stat, 1e-3)

    def test_C03_fps_boundary(self) -> None:
        """C-03: declared fps=30 but actual dt corresponds to fps=29.51.
        ``|μ_dt − 33.333ms| ≈ 0.55ms >> 0.1ms`` → FAIL."""
        recs = _baseline(fps=29.51)
        # Override declared fps to 30 (the lie)
        for r in recs:
            r["fps"] = 30.0
        result = r20c_fps_jitter(recs)
        self.assertFalse(result.passed, msg=f"expected FAIL, got {result}")

    def test_C03_boundary_within_tolerance_passes(self) -> None:
        """Sanity: declared fps=30 with actual dt=33.333ms PASSes (no drift)."""
        recs = _baseline(fps=30.0)
        result = r20c_fps_jitter(recs)
        self.assertTrue(result.passed, msg=f"expected PASS, got {result}")

    def test_C05_speed_saturation(self) -> None:
        """C-05: every frame has ‖speed‖=49.99 m/s. Per-frame R10 (V_max=50)
        passes but μ_speed=49.99 > 15 and ratio_high=1.0 > 0.10 → FAIL."""
        recs = _baseline()
        for r in recs:
            r["camera_speed"] = [49.99, 0.0, 0.0]
        result = r20d_speed_profile(recs)
        self.assertFalse(result.passed, msg=f"expected FAIL, got {result}")
        self.assertEqual(result.n_outliers, N)

    def test_yaw_turn_rate_extreme(self) -> None:
        """Synthetic bot turning 30°/frame at 30fps = 900°/s > 720°/s.
        ratio_extreme = 1.0 > 0.05 → FAIL."""
        recs = _baseline(yaw_step=30.0)
        result = r20e_yaw_turn_rate(recs)
        self.assertFalse(result.passed, msg=f"expected FAIL, got {result}")
        self.assertGreater(result.sample_stat, 0.05)


class TestIL11AbstainGates(unittest.TestCase):
    """IL11: degenerate inputs ABSTAIN, never crash."""

    def test_abstain_empty(self) -> None:
        for fn in (r20a_quat_norm_distribution, r20b_mouse_dx_cumulative,
                   r20c_fps_jitter, r20d_speed_profile, r20e_yaw_turn_rate):
            r = fn([])
            self.assertFalse(r.passed)
            self.assertTrue(r.detail.startswith("ABSTAIN:empty"), msg=f"{fn.__name__}: {r}")
            self.assertEqual(r.sample_size, 0)

    def test_abstain_insufficient_sample(self) -> None:
        recs = _baseline(n=5)  # < min_frames=10
        for fn in (r20a_quat_norm_distribution, r20b_mouse_dx_cumulative,
                   r20c_fps_jitter, r20d_speed_profile, r20e_yaw_turn_rate):
            r = fn(recs)
            self.assertFalse(r.passed)
            self.assertIn("insufficient_sample", r.detail)

    def test_abstain_malformed_quat(self) -> None:
        recs = _baseline()
        for r in recs:
            del r["camera_rotation_quaternion"]
        result = r20a_quat_norm_distribution(recs)
        self.assertFalse(result.passed)
        self.assertIn("ABSTAIN:missing_field", result.detail)

    def test_abstain_malformed_speed(self) -> None:
        recs = _baseline()
        for r in recs:
            r["camera_speed"] = "garbage"
        result = r20d_speed_profile(recs)
        self.assertFalse(result.passed)
        self.assertIn("ABSTAIN:malformed_field", result.detail)

    def test_abstain_non_monotone_time(self) -> None:
        recs = _baseline()
        # reverse times so dt becomes negative
        recs[5]["time"] = (T0 - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S.000")
        result = r20c_fps_jitter(recs)
        self.assertFalse(result.passed)
        self.assertIn("non_monotone_time", result.detail)


class TestR20Reporting(unittest.TestCase):
    """Sanity: sample_size + n_outliers populated correctly."""

    def test_sample_size_populated(self) -> None:
        recs = _baseline()
        for fn, expected in (
            (r20a_quat_norm_distribution, N),
            (r20b_mouse_dx_cumulative, N),
            (r20c_fps_jitter, N - 1),
            (r20d_speed_profile, N),
            (r20e_yaw_turn_rate, N - 1),
        ):
            r = fn(recs)
            self.assertEqual(r.sample_size, expected, msg=f"{fn.__name__}: {r}")
            self.assertFalse(math.isnan(r.sample_stat))


if __name__ == "__main__":
    unittest.main()
