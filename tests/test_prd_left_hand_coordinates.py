#!/usr/bin/env python3
"""Tests for bin/prd_test_left_hand_coordinates.py"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_left_hand_coordinates.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Basic CLI tests
# ---------------------------------------------------------------------------

def test_script_exists():
    """Test that the script exists and can be imported."""
    assert SCRIPT.exists(), f"Script not found: {SCRIPT}"
    
    import importlib.util
    spec = importlib.util.spec_from_file_location("left_hand_coordinates", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    
    try:
        spec.loader.exec_module(module)
        assert hasattr(module, 'main'), "Module should have main function"
        assert hasattr(module, 'create_unit_axes'), "Module should have create_unit_axes function"
        assert hasattr(module, 'compute_handedness_sign'), "Module should have compute_handedness_sign function"
        assert hasattr(module, 'test_left_handed_cross_products'), "Module should have test_left_handed_cross_products function"
    except ImportError as e:
        print(f"Note: Some imports failed during test: {e}")


def test_help():
    """Test that the script shows help when run with --help."""
    result = _run(["--help"])
    assert result.returncode == 0, f"Script should exit with 0 when showing help"
    assert "usage:" in result.stdout.lower() or "Usage:" in result.stdout
    assert "left-hand" in result.stdout.lower() or "coordinate" in result.stdout


def test_verbose_flag():
    """Test that the script runs with --verbose flag."""
    result = _run(["--verbose"])
    # Should exit 0 if left-handed validation passes
    assert result.returncode == 0, f"Script should pass with verbose flag, got: {result.stderr}"


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------

class TestCreateUnitAxes:
    """Tests for create_unit_axes function."""

    def _create(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("left_hand_coordinates", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.create_unit_axes()

    def test_returns_three_arrays(self):
        """create_unit_axes should return three arrays."""
        result = self._create()
        assert len(result) == 3

    def test_x_axis_is_unit_vector(self):
        """X axis should be a unit vector."""
        import numpy as np
        x, _, _ = self._create()
        assert np.isclose(np.linalg.norm(x), 1.0), "X axis should be unit length"

    def test_y_axis_is_unit_vector(self):
        """Y axis should be a unit vector."""
        import numpy as np
        _, y, _ = self._create()
        assert np.isclose(np.linalg.norm(y), 1.0), "Y axis should be unit length"

    def test_z_axis_is_unit_vector(self):
        """Z axis should be a unit vector."""
        import numpy as np
        _, _, z = self._create()
        assert np.isclose(np.linalg.norm(z), 1.0), "Z axis should be unit length"

    def test_z_axis_is_negated(self):
        """Z axis should be negative (left-handed system)."""
        import numpy as np
        _, _, z = self._create()
        assert np.isclose(z[2], -1.0), "Z axis should be -1 in z-component for left-handed"


class TestComputeHandednessSign:
    """Tests for compute_handedness_sign function."""

    def _compute(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("left_hand_coordinates", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.compute_handedness_sign()

    def test_returns_negative_for_left_handed(self):
        """compute_handedness_sign should return -1 for left-handed system."""
        result = self._compute()
        assert result == -1, "Left-handed system should return -1"

    def test_consistency(self):
        """Multiple calls should return consistent results."""
        result1 = self._compute()
        result2 = self._compute()
        assert result1 == result2, "Results should be consistent"


class TestLeftHandedCrossProducts:
    """Tests for test_left_handed_cross_products function."""

    def _test(self, verbose: bool = False):
        import importlib.util
        spec = importlib.util.spec_from_file_location("left_hand_coordinates", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.test_left_handed_cross_products(verbose)

    def test_cross_products_pass(self):
        """test_left_handed_cross_products should return True."""
        result = self._test(verbose=False)
        assert result is True, "Cross product tests should pass for left-handed system"

    def test_cross_products_pass_verbose(self):
        """test_left_handed_cross_products should return True with verbose output."""
        result = self._test(verbose=True)
        assert result is True, "Cross product tests should pass with verbose=True"

    def test_x_cross_y_equals_neg_z(self):
        """X × Y should equal -Z in left-handed system."""
        import numpy as np
        
        import importlib.util
        spec = importlib.util.spec_from_file_location("left_hand_coordinates", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        
        x, y, z = mod.create_unit_axes()
        cross_xy = np.cross(x, y)
        
        assert np.allclose(cross_xy, -z), f"X × Y should equal -Z, got {cross_xy}"

    def test_y_cross_z_equals_neg_x(self):
        """Y × Z should equal -X in left-handed system."""
        import numpy as np
        
        import importlib.util
        spec = importlib.util.spec_from_file_location("left_hand_coordinates", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        
        x, y, z = mod.create_unit_axes()
        cross_yz = np.cross(y, z)
        
        assert np.allclose(cross_yz, -x), f"Y × Z should equal -X, got {cross_yz}"

    def test_z_cross_x_equals_neg_y(self):
        """Z × X should equal -Y in left-handed system."""
        import numpy as np
        
        import importlib.util
        spec = importlib.util.spec_from_file_location("left_hand_coordinates", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        
        x, y, z = mod.create_unit_axes()
        cross_zx = np.cross(z, x)
        
        assert np.allclose(cross_zx, -y), f"Z × X should equal -Y, got {cross_zx}"


class TestAssertLeftHandedCoordinates:
    """Tests for assert_left_handed_coordinates function."""

    def _assert(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("left_hand_coordinates", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.assert_left_handed_coordinates()

    def test_assertion_passes(self):
        """assert_left_handed_coordinates should not raise for left-handed system."""
        # Should not raise any exception
        self._assert()
