#!/usr/bin/env python3
"""Tests for bin/prd_test_depth_invalid_marker.py"""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_depth_invalid_marker.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Basic CLI tests
# ---------------------------------------------------------------------------

def test_script_exists():
    """Test that the script exists and can be imported."""
    assert SCRIPT.exists(), f"Script not found: {SCRIPT}"
    
    import importlib.util
    spec = importlib.util.spec_from_file_location("depth_invalid_marker", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    
    try:
        spec.loader.exec_module(module)
        assert hasattr(module, 'main'), "Module should have main function"
        assert hasattr(module, 'create_depth_buffer'), "Module should have create_depth_buffer function"
        assert hasattr(module, 'write_exr'), "Module should have write_exr function"
        assert hasattr(module, 'read_exr'), "Module should have read_exr function"
    except ImportError as e:
        print(f"Note: Some imports failed during test: {e}")


def test_help():
    """Test that the script shows help when run with --help."""
    result = _run(["--help"])
    assert result.returncode == 0, f"Script should exit with 0 when showing help"
    assert "usage:" in result.stdout.lower() or "Usage:" in result.stdout


def test_missing_output_dir():
    """Test that the script fails with missing output directory."""
    result = _run(["--output-dir", "/tmp/does_not_exist_12345"])
    assert result.returncode != 0, "Should fail with non-existent directory"


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------

class TestCreateDepthBuffer:
    """Tests for create_depth_buffer function."""

    def _create(self, height: int = 100, width: int = 100, sentinel: str = "zero"):
        import importlib.util
        spec = importlib.util.spec_from_file_location("depth_invalid_marker", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.create_depth_buffer(height, width, sentinel)

    def test_returns_numpy_array(self):
        """create_depth_buffer should return a numpy array."""
        result = self._create()
        assert isinstance(result, np.ndarray)

    def test_correct_shape(self):
        """create_depth_buffer should return array of correct shape."""
        result = self._create(height=50, width=80)
        assert result.shape == (50, 80)

    def test_float32_dtype(self):
        """create_depth_buffer should return float32 array."""
        result = self._create()
        assert result.dtype == np.float32

    def test_zero_sentinel_has_zeros(self):
        """Zero sentinel should produce some zero values."""
        result = self._create(sentinel="zero")
        assert np.any(result == 0.0), "Should contain zero sentinel values"

    def test_nan_sentinel_has_nans(self):
        """NaN sentinel should produce some NaN values."""
        result = self._create(sentinel="nan")
        assert np.any(np.isnan(result)), "Should contain NaN sentinel values"

    def test_deterministic_seed(self):
        """Same seed should produce same output."""
        a = self._create()
        b = self._create()
        assert np.array_equal(a, b), "Should be deterministic with fixed seed"


class TestWriteReadEXR:
    """Tests for write_exr and read_exr functions."""

    def _module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("depth_invalid_marker", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_write_read_npz_fallback(self):
        """write_exr/read_exr should work with NPZ fallback."""
        mod = self._module()
        original = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        with tempfile.NamedTemporaryFile(suffix=".exr", delete=False) as f:
            filepath = Path(f.name)
        try:
            mod.write_exr(filepath, original)
            read_back = mod.read_exr(filepath)
            assert read_back is not None, "Should read back data"
            assert np.allclose(original, read_back), "Roundtrip should preserve values"
        finally:
            filepath.unlink(missing_ok=True)
            filepath.with_suffix(".npz").unlink(missing_ok=True)

    def test_write_returns_true(self):
        """write_exr should return True on success."""
        mod = self._module()
        original = np.array([[1.0, 2.0]], dtype=np.float32)
        with tempfile.NamedTemporaryFile(suffix=".exr", delete=False) as f:
            filepath = Path(f.name)
        try:
            result = mod.write_exr(filepath, original)
            assert result is True
        finally:
            filepath.unlink(missing_ok=True)
            filepath.with_suffix(".npz").unlink(missing_ok=True)


class TestVerifyPreservation:
    """Tests for verify_preservation function."""

    def _verify(self, orig, rest, sentinel: str):
        import importlib.util
        spec = importlib.util.spec_from_file_location("depth_invalid_marker", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.verify_preservation(orig, rest, sentinel)

    def test_preservation_zero(self):
        """verify_preservation should correctly identify preserved zeros."""
        orig = np.array([[0.0, 1.0], [2.0, 0.0]], dtype=np.float32)
        rest = np.array([[0.0, 1.0], [2.0, 0.0]], dtype=np.float32)
        
        result = self._verify(orig, rest, "zero")
        
        assert result["passed"] == True
        assert result["orig_invalid"] == 2
        assert result["rest_invalid"] == 2

    def test_preservation_nan(self):
        """verify_preservation should correctly identify preserved NaNs."""
        orig = np.array([[np.nan, 1.0], [2.0, np.nan]], dtype=np.float32)
        rest = np.array([[np.nan, 1.0], [2.0, np.nan]], dtype=np.float32)
        
        result = self._verify(orig, rest, "nan")
        
        assert result["passed"] == True

    def test_preservation_failed(self):
        """verify_preservation should detect lost sentinel values."""
        orig = np.array([[0.0, 1.0], [2.0, 0.0]], dtype=np.float32)
        rest = np.array([[1.0, 1.0], [2.0, 1.0]], dtype=np.float32)  # zeros lost
        
        result = self._verify(orig, rest, "zero")
        
        assert result["passed"] == False
