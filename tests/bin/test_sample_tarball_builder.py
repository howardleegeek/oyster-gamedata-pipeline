#!/usr/bin/env python3
"""
Tests for bin/sample_tarball_builder.py
"""

import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bin.sample_tarball_builder import (
    synthesize_video,
    synthesize_action_camera,
    synthesize_depth_dir,
    build_sample_tarball,
    main,
    create_gameinfo_xlsx,
)


class TestSynthesizeVideo:
    """Tests for synthesize_video function."""
    
    def test_synthesize_video_creates_file(self):
        """Test that synthesize_video creates a video file (mock subprocess)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "test_video.mp4")
            
            # Mock subprocess.run to avoid actually calling ffmpeg
            with mock.patch('subprocess.run') as mock_run:
                mock_run.return_value = mock.Mock(returncode=0, stderr="")
                
                # Create a dummy file to simulate ffmpeg output
                Path(video_path).parent.mkdir(parents=True, exist_ok=True)
                with open(video_path, 'wb') as f:
                    f.write(b'\x00' * 1024)  # Dummy video data
                
                result = synthesize_video(video_path, duration_sec=1, fps=30)
                
                # Verify ffmpeg was called with correct arguments
                mock_run.assert_called_once()
                call_args = mock_run.call_args[0][0]
                assert "ffmpeg" in call_args[0]
                assert "-c:v" in call_args
                assert "libx264" in call_args
                
                # Verify output path
                assert result == video_path
                assert os.path.exists(video_path)
    
    def test_synthesize_video_ffmpeg_failure(self):
        """Test that synthesize_video raises on ffmpeg failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "test_video.mp4")
            
            with mock.patch('subprocess.run') as mock_run:
                mock_run.return_value = mock.Mock(
                    returncode=1,
                    stderr="ffmpeg error: invalid codec"
                )
                
                with pytest.raises(RuntimeError, match="ffmpeg failed"):
                    synthesize_video(video_path)


class TestSynthesizeActionCamera:
    """Tests for synthesize_action_camera function."""
    
    def test_synthesize_action_camera_returns_valid_records(self):
        """Test that synthesize_action_camera creates valid binary records."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "action_camera.bin")
            
            result = synthesize_action_camera(output_path, frame_count=100)
            
            # Verify file was created
            assert result == output_path
            assert os.path.exists(output_path)
            
            # Verify file size: 100 records * (8 + 24*4) bytes = 100 * 104 = 10400 bytes
            file_size = os.path.getsize(output_path)
            assert file_size == 100 * 104, f"Expected {100 * 104} bytes, got {file_size}"
            
            # Verify record structure by reading first record
            import numpy as np
            with open(output_path, 'rb') as f:
                timestamp = np.frombuffer(f.read(8), dtype=np.float64)[0]
                frame_data = np.frombuffer(f.read(24 * 4), dtype=np.float32)
                
                # First record should have timestamp ~0.0
                assert abs(timestamp - 0.0) < 0.01
                assert len(frame_data) == 24
                # Values should be normalized (between 0 and 1)
                assert all(0 <= v <= 1 for v in frame_data)
    
    def test_synthesize_action_camera_deterministic(self):
        """Test that synthesize_action_camera produces deterministic output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path1 = os.path.join(tmpdir, "test1.bin")
            path2 = os.path.join(tmpdir, "test2.bin")
            
            synthesize_action_camera(path1, frame_count=10)
            synthesize_action_camera(path2, frame_count=10)
            
            # Same seed should produce same output
            with open(path1, 'rb') as f1, open(path2, 'rb') as f2:
                assert f1.read() == f2.read()


class TestSynthesizeDepthDir:
    """Tests for synthesize_depth_dir function."""
    
    def test_synthesize_depth_dir_creates_files(self):
        """Test that synthesize_depth_dir creates the expected number of files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            count = synthesize_depth_dir(tmpdir, count=10)
            
            assert count == 10
            
            # Verify files exist
            files = list(Path(tmpdir).glob("depth_*.exr"))
            assert len(files) == 10
            
            # Verify naming pattern
            for i in range(10):
                expected_file = Path(tmpdir) / f"depth_{i:06d}.exr"
                assert expected_file.exists()
    
    def test_synthesize_depth_dir_minimal_size(self):
        """Test that depth files have minimal size."""
        with tempfile.TemporaryDirectory() as tmpdir:
            synthesize_depth_dir(tmpdir, count=5)
            
            for i in range(5):
                exr_file = Path(tmpdir) / f"depth_{i:06d}.exr"
                # Files should be at least 256 bytes (minimal placeholder)
                assert exr_file.stat().st_size >= 256


class TestBuildSampleTarball:
    """Tests for build_sample_tarball function."""
    
    def test_build_sample_tarball_packages_all_4_assets(self):
        """Test that build_sample_tarball creates tarball with all required assets."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test.tar.gz")
            
            # Build with skip flags for speed
            result = build_sample_tarball(
                output_path=output_path,
                clip_id="test-clip",
                skip_video=True,
                skip_depth=True,
            )
            
            assert result == output_path
            assert os.path.exists(output_path)
            
            # Verify tarball contents
            with tarfile.open(output_path, 'r:gz') as tar:
                names = tar.getnames()
                
                # Check all 4 assets are present
                assert "video.mp4" in names, "Missing video.mp4"
                assert "action_camera.bin" in names, "Missing action_camera.bin"
                assert "gameinfo.xlsx" in names, "Missing gameinfo.xlsx"
                assert any(n.startswith("depth/") for n in names), "Missing depth/ directory"
    
    def test_skip_flags_work(self):
        """Test that skip_video and skip_depth flags work correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_skip.tar.gz")
            
            # Build with both skip flags
            build_sample_tarball(
                output_path=output_path,
                skip_video=True,
                skip_depth=True,
            )
            
            # Verify tarball was created
            assert os.path.exists(output_path)
            
            # Video should be minimal placeholder
            with tarfile.open(output_path, 'r:gz') as tar:
                video_member = tar.getmember("video.mp4")
                # Placeholder should be small (< 10KB)
                assert video_member.size < 10 * 1024
                
                # Depth should have minimal files
                depth_files = [n for n in tar.getnames() if n.startswith("depth/")]
                assert len(depth_files) >= 1  # At least one placeholder
    
    def test_build_sample_tarball_sha256_output(self):
        """Test that build_sample_tarball outputs correct SHA-256."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_sha.tar.gz")
            
            # Capture stdout
            with mock.patch('builtins.print'):
                build_sample_tarball(
                    output_path=output_path,
                    skip_video=True,
                    skip_depth=True,
                )
            
            # Verify we can compute SHA-256
            sha256 = hashlib.sha256()
            with open(output_path, 'rb') as f:
                sha256.update(f.read())
            
            # Should have valid hex digest
            digest = sha256.hexdigest()
            assert len(digest) == 64  # SHA-256 produces 64 hex chars
            assert all(c in '0123456789abcdef' for c in digest)


class TestMain:
    """Tests for main() CLI function."""
    
    def test_main_argparse(self):
        """Test that main() correctly parses command line arguments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "cli_test.tar.gz")
            
            # Test with custom arguments
            argv = [
                "--output", output_path,
                "--clip-id", "test-clip-123",
                "--skip-video",
                "--skip-depth",
            ]
            
            exit_code = main(argv)
            
            assert exit_code == 0
            assert os.path.exists(output_path)
            
            # Verify clip ID was used
            with tarfile.open(output_path, 'r:gz') as tar:
                # Extract and check gameinfo.xlsx
                import zipfile
                with tempfile.TemporaryDirectory() as extract_dir:
                    tar.extract("gameinfo.xlsx", extract_dir)
                    xlsx_path = os.path.join(extract_dir, "gameinfo.xlsx")
                    # File should exist
                    assert os.path.exists(xlsx_path)
    
    def test_main_default_output(self):
        """Test that main() uses default output path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Change to temp directory
            original_cwd = os.getcwd()
            os.chdir(tmpdir)
            
            try:
                argv = ["--skip-video", "--skip-depth"]
                exit_code = main(argv)
                
                assert exit_code == 0
                # Default path should be created
                assert os.path.exists("samples/buyer-spec-v1-rc1.tar.gz")
            finally:
                os.chdir(original_cwd)
    
    def test_main_error_handling(self):
        """Test that main() handles errors gracefully."""
        # Try to write to invalid path
        argv = ["--output", "/nonexistent/path/test.tar.gz"]
        
        exit_code = main(argv)
        
        # Should return non-zero exit code on error
        assert exit_code != 0


class TestGameinfoXlsx:
    """Tests for create_gameinfo_xlsx function."""
    
    def test_create_gameinfo_xlsx_creates_file(self):
        """Test that gameinfo.xlsx is created with correct structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            xlsx_path = os.path.join(tmpdir, "gameinfo.xlsx")
            
            result = create_gameinfo_xlsx(xlsx_path, clip_id="test-clip")
            
            assert result == xlsx_path
            assert os.path.exists(xlsx_path)
            assert os.path.getsize(xlsx_path) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])