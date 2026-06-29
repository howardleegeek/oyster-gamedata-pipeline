#!/usr/bin/env python3
"""
Tests for bin/prd_test_left_hand_coordinates.py

PRD p3 #4: Left-hand coordinate system validation.
Validates that the coordinate system follows left-handed convention
via cross-product sign on Vector3 axes.

Note: NumPy uses right-handed coordinates by default, so this test
script is expected to fail (return 1) on standard NumPy. This is the
intended behavior - it alerts the operator that the coordinate system
is not left-handed.
"""

import numpy as np
import pytest

from bin.prd_test_left_hand_coordinates import (
    assert_left_handed_coordinates,
    compute_handedness_sign,
    create_unit_axes,
    test_left_handed_cross_products,
)


class TestCreateUnitAxes:
    """Tests for create_unit_axes function."""

    def test_returns_three_vectors(self):
        """Test that function returns exactly 3 vectors."""
        x, y, z = create_unit_axes()
        assert x.shape == (3,)
        assert y.shape == (3,)
        assert z.shape == (3,)

    def test_x_axis_is_unit(self):
        """Test X axis is unit vector [1, 0, 0]."""
        x, _, _ = create_unit_axes()
        np.testing.assert_array_almost_equal(x, [1.0, 0.0, 0.0])

    def test_y_axis_is_unit(self):
        """Test Y axis is unit vector [0, 1, 0]."""
        _, y, _ = create_unit_axes()
        np.testing.assert_array_almost_equal(y, [0.0, 1.0, 0.0])

    def test_z_axis_is_unit(self):
        """Test Z axis is unit vector [0, 0, 1]."""
        _, _, z = create_unit_axes()
        np.testing.assert_array_almost_equal(z, [0.0, 0.0, 1.0])

    def test_axes_are_orthogonal(self):
        """Test that axes are orthogonal."""
        x, y, z = create_unit_axes()
        assert np.dot(x, y) == 0.0
        assert np.dot(y, z) == 0.0
        assert np.dot(z, x) == 0.0


class TestComputeHandednessSign:
    """Tests for compute_handedness_sign function."""

    def test_returns_plus_one_for_right_handed(self):
        """Test that NumPy's right-handed system returns +1."""
        result = compute_handedness_sign()
        # NumPy uses right-handed cross products by default
        assert result == 1

    def test_sign_is_deterministic(self):
        """Test that handedness sign is consistent across calls."""
        result1 = compute_handedness_sign()
        result2 = compute_handedness_sign()
        assert result1 == result2


class TestLeftHandedCrossProducts:
    """Tests for test_left_handed_cross_products function.

    Note: NumPy uses right-handed coordinates, so these tests document
    that the function correctly identifies the system as NOT left-handed.
    """

    def test_x_cross_y_is_plus_z_for_numpy(self):
        """Test NumPy X × Y = +Z (right-handed)."""
        x, y, z = create_unit_axes()
        cross_xy = np.cross(x, y)
        # NumPy uses right-handed: X × Y = +Z
        np.testing.assert_array_almost_equal(cross_xy, z)

    def test_y_cross_z_is_plus_x_for_numpy(self):
        """Test NumPy Y × Z = +X (right-handed)."""
        x, y, z = create_unit_axes()
        cross_yz = np.cross(y, z)
        # NumPy uses right-handed: Y × Z = +X
        np.testing.assert_array_almost_equal(cross_yz, x)

    def test_z_cross_x_is_plus_y_for_numpy(self):
        """Test NumPy Z × X = +Y (right-handed)."""
        x, y, z = create_unit_axes()
        cross_zx = np.cross(z, x)
        # NumPy uses right-handed: Z × X = +Y
        np.testing.assert_array_almost_equal(cross_zx, y)

    def test_returns_false_for_right_handed_numpy(self):
        """Test that function returns False for NumPy's right-handed system."""
        result = test_left_handed_cross_products(verbose=False)
        assert result is False  # NumPy is right-handed, not left-handed

    def test_verbose_mode_prints_results(self, capsys):
        """Test that verbose mode prints test results."""
        test_left_handed_cross_products(verbose=True)
        captured = capsys.readouterr()
        assert "Test X × Y = -Z" in captured.out
        assert "Test Y × Z = -X" in captured.out
        assert "Test Z × X = -Y" in captured.out
        assert "FAIL" in captured.out  # Should show failures for NumPy

    def test_verbose_shows_right_handed_result(self, capsys):
        """Test verbose output indicates right-handed system."""
        test_left_handed_cross_products(verbose=True)
        captured = capsys.readouterr()
        # Should show both expected (-Z, -X, -Y) and actual (+Z, +X, +Y)
        assert "-Z" in captured.out
        assert "+Z" in captured.out or "FAIL" in captured.out


class TestAssertLeftHandedCoordinates:
    """Tests for assert_left_handed_coordinates function.

    Since NumPy is right-handed, these tests verify the assertion
    correctly raises for non-left-handed systems.
    """

    def test_raises_assertion_error_on_right_handed(self):
        """Test that assertion fails (raises) for NumPy's right-handed system."""
        # NumPy is right-handed, so this should raise AssertionError
        with pytest.raises(AssertionError, match="NOT left-handed"):
            assert_left_handed_coordinates()

    def test_error_message_contains_expected_formula(self):
        """Test that error message contains the expected cross-product formulas."""
        with pytest.raises(AssertionError, match="X × Y = -Z"):
            assert_left_handed_coordinates()


class TestLeftHandedFormulas:
    """Tests documenting what left-handed cross products SHOULD be.

    These verify the mathematical definition, not NumPy's behavior.
    """

    def test_left_handed_x_cross_y_equals_minus_z(self):
        """Mathematical definition: left-handed X × Y = -Z."""
        x = np.array([1.0, 0.0, 0.0])
        y = np.array([0.0, 1.0, 0.0])
        z = np.array([0.0, 0.0, 1.0])

        # For a truly left-handed system: X × Y should equal -Z
        # This is what the PRD expects from the game data
        expected_left_handed = -z

        # NumPy's actual result (right-handed)
        actual = np.cross(x, y)

        # They should NOT match (NumPy is right-handed)
        assert not np.allclose(actual, expected_left_handed)
        # NumPy gives +Z, but left-handed needs -Z
        np.testing.assert_array_almost_equal(actual, -expected_left_handed)

    def test_left_handed_y_cross_z_equals_minus_x(self):
        """Mathematical definition: left-handed Y × Z = -X."""
        x = np.array([1.0, 0.0, 0.0])
        y = np.array([0.0, 1.0, 0.0])
        z = np.array([0.0, 0.0, 1.0])

        expected_left_handed = -x
        actual = np.cross(y, z)

        assert not np.allclose(actual, expected_left_handed)

    def test_left_handed_z_cross_x_equals_minus_y(self):
        """Mathematical definition: left-handed Z × X = -Y."""
        x = np.array([1.0, 0.0, 0.0])
        y = np.array([0.0, 1.0, 0.0])
        z = np.array([0.0, 0.0, 1.0])

        expected_left_handed = -y
        actual = np.cross(z, x)

        assert not np.allclose(actual, expected_left_handed)


class TestScriptExitCode:
    """Tests verifying the script's CLI exit behavior."""

    def test_main_returns_1_on_right_handed_system(self):
        """Test that main() returns exit code 1 for right-handed NumPy."""
        from bin.prd_test_left_hand_coordinates import main

        # Run with default args (no verbose)
        exit_code = main([])
        assert exit_code == 1  # Fail because NumPy is right-handed

    def test_main_with_verbose_returns_1(self):
        """Test that main() with verbose flag also returns 1."""
        from bin.prd_test_left_hand_coordinates import main

        exit_code = main(["--verbose"])
        assert exit_code == 1
