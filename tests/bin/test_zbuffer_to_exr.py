#!/usr/bin/env python3
"""Test coverage for bin/zbuffer_to_exr.py (G221 depth buffer to EXR conversion)."""

import json
import os
import tempfile
from unittest.mock import patch

import numpy as np
import pytest


class TestReadF32File:
    """Tests for read_f32_file function."""

    def test_read_f32_basic(self):
        """Test reading a basic float32 file."""
        from bin.zbuffer_to_exr import read_f32_file

        # Create a small test file (10x10)
        width, height = 10, 10
        test_data = np.arange(width * height, dtype=np.float32)
        
        with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as f:
            f.write(test_data.tobytes())
            filepath = f.name

        try:
            result = read_f32_file(filepath, width=width, height=height)
            assert result.shape == (height, width)
            np.testing.assert_array_equal(result, test_data.reshape(height, width))
        finally:
            os.unlink(filepath)

    def test_read_f32_with_specific_dimensions(self):
        """Test reading with custom width/height."""
        from bin.zbuffer_to_exr import read_f32_file

        width, height = 640, 480
        test_data = np.zeros(width * height, dtype=np.float32)
        
        with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as f:
            f.write(test_data.tobytes())
            filepath = f.name

        try:
            result = read_f32_file(filepath, width=width, height=height)
            assert result.shape == (height, width)
        finally:
            os.unlink(filepath)


class TestWriteExrFile:
    """Tests for write_exr_file function."""

    def test_write_exr_fallback_without_openexr(self):
        """Test write_exr_file when OpenEXR is not available."""
        from bin import zbuffer_to_exr

        with patch.object(zbuffer_to_exr, "HAS_EXR", False):
            depth_data = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
            
            with tempfile.TemporaryDirectory() as tmpdir:
                filepath = os.path.join(tmpdir, "test.exr")
                zbuffer_to_exr.write_exr_file(filepath, depth_data, width=2, height=2)
                
                # Should create a .npy file instead
                npy_filepath = filepath.replace(".exr", ".npy")
                assert os.path.exists(npy_filepath)
                loaded = np.load(npy_filepath)
                np.testing.assert_array_equal(loaded, depth_data)

    def test_write_exr_with_openexr(self):
        """Test write_exr_file when OpenEXR is available (mocked)."""
        from bin import zbuffer_to_exr

        # Mock OpenEXR to avoid actual file creation
        mock_exr = pytest.importorskip("OpenEXR", reason="OpenEXR not installed")
        
        depth_data = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.exr")
            zbuffer_to_exr.write_exr_file(filepath, depth_data, width=2, height=2)
            
            # File should exist
            assert os.path.exists(filepath)


class TestCreateSourceMarker:
    """Tests for create_source_marker function."""

    def test_create_source_marker_basic(self):
        """Test creating the source marker file."""
        from bin.zbuffer_to_exr import create_source_marker

        with tempfile.TemporaryDirectory() as tmpdir:
            create_source_marker(tmpdir)
            
            marker_path = os.path.join(tmpdir, ".source")
            assert os.path.exists(marker_path)
            
            with open(marker_path, "r") as f:
                marker = json.load(f)
            
            assert marker["kind"] == "engine_zbuffer"
            assert marker["units"] == "meters"
            assert marker["format"] == "exr"
            assert marker["dtype"] == "float32"
            assert marker["channels"] == ["Z"]
            assert marker["resolution"] == [1920, 1080]
            assert marker["linearized"] is True
            assert marker["coordinate_system"] == "view_space"

    def test_create_source_marker_overwrites(self):
        """Test that create_source_marker overwrites existing file."""
        from bin.zbuffer_to_exr import create_source_marker

        with tempfile.TemporaryDirectory() as tmpdir:
            marker_path = os.path.join(tmpdir, ".source")
            
            # Create initial marker
            with open(marker_path, "w") as f:
                json.dump({"old": "data"}, f)
            
            # Overwrite
            create_source_marker(tmpdir)
            
            with open(marker_path, "r") as f:
                marker = json.load(f)
            
            assert marker["kind"] == "engine_zbuffer"


class TestMain:
    """Tests for main function."""

    def test_main_missing_active_session(self):
        """Test main exits when active_session directory is missing."""
        from bin.zbuffer_to_exr import main
        
        with patch("pathlib.Path.exists", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_missing_depth_raw_dir(self):
        """Test main exits when depth_raw directory is missing."""
        from bin.zbuffer_to_exr import main

        # Mock Path.exists to return True for active_session, False for depth_raw
        # Must use precise path matching to avoid false positives (e.g., "depth" matching "depth_raw")
        def mock_exists(self):
            path_str = str(self)
            # Check for exact path components, not substrings
            if path_str == "active_session":
                return True
            if path_str.endswith("active_session/depth_raw"):
                return False
            if path_str.endswith("active_session/depth"):
                return True  # output dir exists
            return False

        with patch("pathlib.Path.exists", mock_exists):
            # The actual behavior is to exit with code 1 (error) when depth_raw is missing
            with pytest.raises(SystemExit) as exc_info:
                main()
            # Code exits with 1 (error) when depth_raw doesn't exist
            assert exc_info.value.code == 1
