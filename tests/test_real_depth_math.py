"""
G198 — Python mirror of mc-mod/.../depth/DepthMath.java.

The Mac dev box has no JDK + LWJGL to run the Java unit tests, so the
projection-inversion math lives in two parallel implementations that MUST
stay in sync:

* Java:  mc-mod/src/main/java/world/oyster/recorder/depth/DepthMath.java
* Python: bin/real_depth_validator.py (helpers used by the lint integration)

This test file pins the contract — when these tests pass we know the Python
mirror agrees with the Java source-of-truth on every edge case the PRD §3.4
calls out (sky / clipped / NaN / out-of-range / classic-z / reversed-z).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from bin.real_depth_validator import (
    INVALID_DEPTH_THRESHOLD,
    MAX_INVALID_RATIO_HARD,
    MAX_INVALID_RATIO_WARN,
    count_invalid,
    linear_depth_classic,
    linear_depth_reversed,
    linearize_buffer,
)

# ---------------------------------------------------------------- classic-z


class TestLinearDepthClassic:
    """Per-pixel scalar inversion matching the Java DepthMath.linearDepthClassic."""

    def test_near_plane_returns_near(self) -> None:
        # z_buf = 0 → z_ndc = -1 → denom = (far+near) + (far-near) = 2*far
        # → linear = (2*near*far) / (2*far) = near
        assert math.isclose(linear_depth_classic(0.0, 0.5, 30.0), 0.5, rel_tol=1e-5)

    def test_far_plane_minus_one_returns_below_far(self) -> None:
        # z_buf = 0.99, near 0.5, far 30: perspective inversion lands roughly
        # at (2*0.5*30)/((30+0.5) - 0.98*(30-0.5)) = 30/1.59 = 18.87 m.
        # This is well below the far plane because OpenGL's z is heavily
        # weighted near the near plane — most of the [0, 1] range maps to
        # the back half of the view frustum. The point of this test is to
        # confirm the value is strictly between near and far.
        v = linear_depth_classic(0.99, 0.5, 30.0)
        assert 0.5 < v < 30.0
        # And the value must be ≥ a sane fraction of the far plane.
        assert v > 5.0

    def test_sky_invalid_threshold_returns_zero(self) -> None:
        # Anything >= 0.999 is invalid per PRD §3.4.
        assert linear_depth_classic(0.999, 0.5, 30.0) == 0.0
        assert linear_depth_classic(0.9995, 0.5, 30.0) == 0.0
        assert linear_depth_classic(1.0, 0.5, 30.0) == 0.0

    def test_below_zero_is_invalid(self) -> None:
        assert linear_depth_classic(-0.1, 0.5, 30.0) == 0.0

    def test_above_one_is_invalid(self) -> None:
        assert linear_depth_classic(1.1, 0.5, 30.0) == 0.0

    def test_nan_is_invalid(self) -> None:
        assert linear_depth_classic(float("nan"), 0.5, 30.0) == 0.0

    def test_inf_is_invalid(self) -> None:
        assert linear_depth_classic(float("inf"), 0.5, 30.0) == 0.0

    def test_invalid_planes_return_zero(self) -> None:
        # near <= 0 or far <= near → invalid config → 0
        assert linear_depth_classic(0.5, 0.0, 30.0) == 0.0
        assert linear_depth_classic(0.5, -1.0, 30.0) == 0.0
        assert linear_depth_classic(0.5, 30.0, 30.0) == 0.0
        assert linear_depth_classic(0.5, 30.0, 10.0) == 0.0

    def test_inversion_monotonic(self) -> None:
        # Bigger z_buf must produce bigger linear depth (until invalid).
        prev = -1.0
        for z in np.linspace(0.0, 0.99, 50):
            v = linear_depth_classic(float(z), 0.5, 30.0)
            assert v > prev, f"non-monotonic at z={z}: {v} <= {prev}"
            prev = v

    def test_round_trip_via_projection(self) -> None:
        """Take a few specific metric depths, project to NDC, project back."""
        near, far = 0.5, 30.0
        for metric in [1.0, 5.0, 10.0, 25.0]:
            # Forward: z_ndc = (far+near)/(far-near) - (2*near*far)/((far-near)*metric)
            # i.e. solve linear formula for z_ndc.
            z_ndc = ((far + near) / (far - near)) - ((2.0 * near * far) / ((far - near) * metric))
            z_buf = (z_ndc + 1.0) / 2.0
            recovered = linear_depth_classic(z_buf, near, far)
            assert math.isclose(
                recovered, metric, rel_tol=1e-3
            ), f"round-trip failed: metric={metric} → z_buf={z_buf} → recovered={recovered}"


# ---------------------------------------------------------------- reversed-z


class TestLinearDepthReversed:
    """Symmetric to classic — near=1, far=0 in the buffer."""

    def test_near_plane_returns_near(self) -> None:
        # Reversed convention: z=1 is the near plane. After 1-z flip it
        # becomes z=0 in classic-z, which evaluates to the near metre value.
        v = linear_depth_reversed(1.0, 0.5, 30.0)
        assert math.isclose(v, 0.5, rel_tol=1e-5)

    def test_far_plane_invalid(self) -> None:
        # Reversed: z=0 is far/sky. After flip = 1.0 → invalid threshold.
        assert linear_depth_reversed(0.0, 0.5, 30.0) == 0.0

    def test_clip_invalid_threshold(self) -> None:
        # Reversed: z just above 0 means just below sky. After flip = ~1.0
        # but below the invalid threshold? 0.0005 → flip = 0.9995 → invalid.
        assert linear_depth_reversed(0.0005, 0.5, 30.0) == 0.0

    def test_out_of_range_invalid(self) -> None:
        assert linear_depth_reversed(-0.1, 0.5, 30.0) == 0.0
        assert linear_depth_reversed(1.1, 0.5, 30.0) == 0.0
        assert linear_depth_reversed(float("nan"), 0.5, 30.0) == 0.0


# ---------------------------------------------------------------- vectorised


class TestLinearizeBuffer:
    """Buffer-wide conversion used by the writer thread."""

    def test_known_shape(self) -> None:
        depth = np.array([0.0, 0.5, 0.9, 0.999, 1.0], dtype=np.float32)
        out = linearize_buffer(depth, near=0.5, far=30.0, reversed_z=False)
        assert out.shape == depth.shape
        assert out.dtype == np.float32
        assert out[0] == pytest.approx(0.5)
        assert out[3] == 0.0  # sky
        assert out[4] == 0.0  # clip-far

    def test_invalid_ratio_zero_on_in_range_buffer(self) -> None:
        # All-valid buffer: 0.01..0.5
        depth = np.linspace(0.01, 0.5, 100, dtype=np.float32)
        out = linearize_buffer(depth, near=0.5, far=30.0, reversed_z=False)
        ratio = float(np.mean(out == 0.0))
        assert ratio == 0.0

    def test_sky_heavy_buffer_high_invalid(self) -> None:
        # 50/50 sky vs valid → ratio ≈ 0.5
        bad = np.full(50, 0.9999, dtype=np.float32)
        good = np.full(50, 0.5, dtype=np.float32)
        depth = np.concatenate([bad, good])
        out = linearize_buffer(depth, near=0.5, far=30.0, reversed_z=False)
        ratio = float(np.mean(out == 0.0))
        assert math.isclose(ratio, 0.5, abs_tol=1e-9)

    def test_reversed_z_path(self) -> None:
        depth = np.array([1.0, 0.5, 0.1, 0.001, 0.0], dtype=np.float32)
        out = linearize_buffer(depth, near=0.5, far=30.0, reversed_z=True)
        # 1.0 → near plane → near (0.5)
        assert math.isclose(out[0], 0.5, rel_tol=1e-5)
        # 0.0 → sky → 0
        assert out[4] == 0.0
        # 0.001 → 1 - 0.001 = 0.999 → sky threshold → 0
        assert out[3] == 0.0


# ---------------------------------------------------------------- constants


class TestConstants:
    """Pin the invalid-pixel thresholds against the Java source-of-truth."""

    def test_invalid_threshold_matches_java(self) -> None:
        # mc-mod/.../depth/DepthMath.java line: INVALID_DEPTH_THRESHOLD = 0.999f
        assert INVALID_DEPTH_THRESHOLD == 0.999

    def test_max_invalid_ratio_hard_matches_java(self) -> None:
        # mc-mod/.../depth/DepthMath.java line: MAX_INVALID_RATIO_HARD = 0.05f
        # = buyer lint v3 #15 ceiling
        assert MAX_INVALID_RATIO_HARD == 0.05

    def test_max_invalid_ratio_warn_below_hard(self) -> None:
        assert MAX_INVALID_RATIO_WARN < MAX_INVALID_RATIO_HARD

    def test_buyer_threshold_strictly_below_lint(self) -> None:
        # Whatever invalid-ratio the mod emits, it MUST stay below the
        # lint v3 #15 threshold of 5 %. The Java MAX_INVALID_RATIO_HARD
        # equals that ceiling for parity, and warn must be strictly less.
        assert MAX_INVALID_RATIO_WARN < 0.05


# ---------------------------------------------------------------- countInvalid


def test_count_invalid_matches_zero_count() -> None:
    arr = np.array([0.0, 1.0, 2.0, 0.0, 3.0, 0.0], dtype=np.float32)
    assert count_invalid(arr) == 3


def test_count_invalid_empty() -> None:
    arr = np.array([], dtype=np.float32)
    assert count_invalid(arr) == 0
