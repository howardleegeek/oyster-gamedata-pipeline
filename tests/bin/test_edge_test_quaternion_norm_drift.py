#!/usr/bin/env python3
"""Tests for bin/edge_test_quaternion_norm_drift.py — Boundary test for quaternion norm drift.

Verifies that the quaternion norm-drift tolerance check:
- Accepts a perfect unit quaternion (norm == 1.0)
- Tolerates drift up to the configured epsilon (default 1e-3)
- Rejects quaternions whose norm deviates beyond epsilon
- Rejects the zero quaternion
- Accepts pre-normalised quaternions and tiny drift thereof
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = (
    Path(__file__).parent.parent.parent / "bin" / "edge_test_quaternion_norm_drift.py"
)


class TestEdgeTestQuaternionNormDrift:
    """Test suite for edge_test_quaternion_norm_drift.py."""

    def test_script_exists(self):
        """Verify the edge test script exists and is executable."""
        assert EDGE_TEST_SCRIPT.exists(), f"Script not found: {EDGE_TEST_SCRIPT}"

    def test_runs_successfully(self):
        """Verify the edge test runs and exits with success (0)."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Verify output shows success message
        assert "passed" in result.stdout.lower()

    def test_verbose_mode(self):
        """Verify verbose mode prints PASS markers and all 8 cases."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Verbose mode failed: {result.stderr}"
        # Verbose should show PASS/FAIL markers for every case
        assert "[PASS]" in result.stdout
        # All eight case labels should appear
        for label in (
            "perfect_unit",
            "drift_2.5e-5",
            "drift_5e-5_norm~1.0001",
            "drift_0.01_out_of_tolerance",
            "zero_quaternion",
            "slightly_below_unit",
            "normalised_05s",
            "normalised_drift_1e-6",
        ):
            assert label in result.stdout, f"Missing case label: {label}"

    def test_custom_epsilon(self):
        """Verify --epsilon flag changes tolerance threshold."""
        # With a very tight epsilon, drift_0.01_out_of_tolerance still rejected
        # but the tighter bound should still let tiny drift through.
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--epsilon", "0.005", "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Custom epsilon failed: {result.stderr}"
        assert "passed" in result.stdout.lower()


class TestQuaternionNorm:
    """Test suite for quaternion_norm function."""

    def test_unit_quaternion_norm(self):
        """Test norm of perfect unit quaternion is 1.0."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import quaternion_norm

        assert quaternion_norm((1.0, 0.0, 0.0, 0.0)) == pytest.approx(1.0)

    def test_zero_quaternion_norm(self):
        """Test norm of zero quaternion is 0.0."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import quaternion_norm

        assert quaternion_norm((0.0, 0.0, 0.0, 0.0)) == 0.0

    def test_normalised_quarter_turns(self):
        """Test norm of (0.5, 0.5, 0.5, 0.5) is 1.0."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import quaternion_norm

        norm = quaternion_norm((0.5, 0.5, 0.5, 0.5))
        assert norm == pytest.approx(1.0)

    def test_pythagorean_quadruple(self):
        """Test norm of (2, 3, 6, 0) equals sqrt(4+9+36+0) = 7."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import quaternion_norm

        assert quaternion_norm((2.0, 3.0, 6.0, 0.0)) == pytest.approx(7.0)


class TestIsUnitQuaternion:
    """Test suite for is_unit_quaternion function."""

    def test_perfect_unit_is_unit(self):
        """A perfect unit quaternion must be accepted."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import is_unit_quaternion

        assert is_unit_quaternion((1.0, 0.0, 0.0, 0.0)) is True

    def test_zero_is_not_unit(self):
        """The zero quaternion must be rejected."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import is_unit_quaternion

        assert is_unit_quaternion((0.0, 0.0, 0.0, 0.0)) is False

    def test_drift_within_default_epsilon(self):
        """A drift of 5e-5 stays within default 1e-3 epsilon."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import is_unit_quaternion

        # (1+5e-5, 5e-5, 5e-5, 5e-5) has norm ≈ 1.0000500037
        assert is_unit_quaternion((1.0 + 5e-5, 5e-5, 5e-5, 5e-5)) is True

    def test_drift_beyond_epsilon_rejected(self):
        """A drift of 0.01 is well beyond default 1e-3 epsilon."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import is_unit_quaternion

        # (1.01, 0.01, 0.01, 0.01) has norm ≈ 1.0101485
        assert is_unit_quaternion((1.01, 0.01, 0.01, 0.01)) is False

    def test_custom_epsilon_threshold(self):
        """A tighter epsilon should reject a quat that the default accepts."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import is_unit_quaternion

        # delta ≈ 2.5e-4 from unit, exceeds epsilon=1e-5 but inside epsilon=1e-3
        q = (0.99975, 0.0, 0.0, 0.0)
        assert is_unit_quaternion(q, epsilon=1e-3) is True
        assert is_unit_quaternion(q, epsilon=1e-5) is False


class TestDriftQuaternion:
    """Test suite for drift_quaternion function."""

    def test_zero_drift_returns_base(self):
        """A zero drift must return the base unchanged."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import drift_quaternion

        base = (1.0, 0.5, -0.3, 0.7)
        result = drift_quaternion(base, 0.0)
        assert result == base

    def test_uniform_drift_added(self):
        """Each component must increase by the drift amount."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import drift_quaternion

        base = (1.0, 0.0, 0.0, 0.0)
        result = drift_quaternion(base, 0.01)
        assert result == pytest.approx((1.01, 0.01, 0.01, 0.01))

    def test_negative_drift_subtracted(self):
        """A negative drift must decrease each component."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import drift_quaternion

        base = (1.0, 0.0, 0.0, 0.0)
        result = drift_quaternion(base, -0.001)
        assert result == pytest.approx((0.999, -0.001, -0.001, -0.001))


class TestRunTests:
    """Test suite for run_tests function (programmatic entry)."""

    def test_default_epsilon_passes_all(self):
        """With the default epsilon, every case should pass."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import run_tests

        assert run_tests(epsilon=1e-3, verbose=False) == 0

    def test_verbose_output_contains_pass_marker(self):
        """Verbose True should emit at least one [PASS] line."""
        import contextlib
        import io

        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import run_tests

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_tests(epsilon=1e-3, verbose=True)
        out = buf.getvalue()
        assert "[PASS]" in out

    def test_tight_epsilon_increases_failures(self):
        """An extremely tight epsilon should make several cases fail."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_quaternion_norm_drift import run_tests

        # With epsilon=0, the perfect unit still passes, but everything
        # with any float drift must fail.
        failures = run_tests(epsilon=0.0, verbose=False)
        # The zero quaternion also fails; the perfect and (0.5,0.5,0.5,0.5) pass.
        # So at least 5 of the 8 cases should fail (drift_2.5e-5, drift_5e-5,
        # drift_0.01_out_of_tolerance, zero, slightly_below_unit, normalised_drift_1e-6).
        assert failures >= 4
