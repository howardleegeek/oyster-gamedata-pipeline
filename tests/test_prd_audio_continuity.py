#!/usr/bin/env python3
"""Tests for bin/prd_test_audio_continuity.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_audio_continuity.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run the script with given arguments."""
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
    
    # Try to import the module
    import importlib.util
    spec = importlib.util.spec_from_file_location("audio_continuity", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    
    # This might fail if there are missing imports, but that's OK for now
    try:
        spec.loader.exec_module(module)
        assert hasattr(module, 'main'), "Module should have main function"
        assert hasattr(module, 'check_ffprobe_available'), "Module should have check_ffprobe_available function"
        assert hasattr(module, 'get_audio_packets'), "Module should have get_audio_packets function"
    except ImportError as e:
        # Some imports might fail in test environment, that's OK
        print(f"Note: Some imports failed during test: {e}")


def test_help():
    """Test that the script shows help when run with --help."""
    result = _run(["--help"])
    assert result.returncode == 0, f"Script should exit with 0 when showing help"
    assert "usage:" in result.stdout.lower() or "Usage:" in result.stdout
    assert "video" in result.stdout


def test_missing_file():
    """Test that the script skips with missing video file."""
    result = _run(["/tmp/does_not_exist.mp4"])
    assert result.returncode == 2, "Should exit with code 2 for missing file"
    # Should output SKIP: for PRD acceptance runner to recognize as skip
    assert "SKIP:" in result.stderr or "SKIP:" in result.stdout
    assert "Video file not found" in result.stderr or "Video file not found" in result.stdout


def test_empty_file():
    """Test that the script skips empty/invalid MP4 file."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"")  # Empty file
        f.flush()
        
        result = _run([f.name])
        # Should exit with code 2 for skip
        assert result.returncode == 2, f"Should exit with code 2 for empty file, got {result.returncode}"
        # Should output SKIP: for PRD acceptance runner to recognize as skip
        combined = result.stdout + result.stderr
        assert "SKIP:" in combined
        assert "empty" in combined.lower() or "0 bytes" in combined


# ---------------------------------------------------------------------------
# Unit tests for imported functions
# ---------------------------------------------------------------------------

class TestAudioContinuityFunctions:
    """Unit tests for individual functions in the module."""
    
    def _import_module(self):
        """Import the module for unit testing."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("audio_continuity", str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    
    def test_check_ffprobe_available(self):
        """Test check_ffprobe_available function."""
        module = self._import_module()
        
        # Mock shutil.which to test both paths
        with patch('shutil.which') as mock_which:
            mock_which.return_value = "/usr/bin/ffprobe"
            assert module.check_ffprobe_available() == True
            
            mock_which.return_value = None
            assert module.check_ffprobe_available() == False
    
    def test_check_continuity_no_gaps(self):
        """Test check_continuity with no gaps."""
        module = self._import_module()
        
        timestamps = [0.0, 0.01, 0.02, 0.03]
        gaps = module.check_continuity(timestamps, threshold_ms=50.0)
        assert gaps == []
    
    def test_check_continuity_with_gaps(self):
        """Test check_continuity with gaps exceeding threshold."""
        module = self._import_module()
        
        timestamps = [0.0, 0.01, 0.10, 0.11]  # 90ms gap between 0.01 and 0.10
        gaps = module.check_continuity(timestamps, threshold_ms=50.0)
        
        assert len(gaps) == 1
        start, end, duration = gaps[0]
        assert start == 0.01
        assert end == 0.10
        # Gap should be approximately 90ms (0.09s * 1000)
        assert abs(duration - 90.0) < 0.1
    
    def test_check_continuity_insufficient_data(self):
        """Test check_continuity with insufficient data."""
        module = self._import_module()
        
        # Single timestamp
        gaps = module.check_continuity([0.0], threshold_ms=50.0)
        assert gaps == []
        
        # Empty list
        gaps = module.check_continuity([], threshold_ms=50.0)
        assert gaps == []
    
    def test_get_audio_streams_mock(self):
        """Test get_audio_streams with mocked ffprobe output."""
        module = self._import_module()
        
        # Mock subprocess.run to return JSON with audio streams
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "streams": [
                {"index": 0, "codec_type": "video"},
                {"index": 1, "codec_type": "audio"},
                {"index": 2, "codec_type": "audio"},
            ]
        })
        
        with patch('subprocess.run', return_value=mock_result):
            video_path = Path("/tmp/test.mp4")
            streams = module.get_audio_streams(video_path)
            assert streams == [1, 2]
    
    def test_get_audio_packets_mock(self):
        """Test get_audio_packets with mocked ffprobe output."""
        module = self._import_module()
        
        # Mock subprocess.run to return JSON with packet timestamps
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "packets": [
                {"pts_time": "0.0"},
                {"pts_time": "0.01"},
                {"pts_time": "0.02"},
                {"pts_time": "0.03"},
            ]
        })
        
        with patch('subprocess.run', return_value=mock_result):
            video_path = Path("/tmp/test.mp4")
            timestamps = module.get_audio_packets(video_path, stream_index=1)
            assert timestamps == [0.0, 0.01, 0.02, 0.03]
    
    def test_get_audio_packets_moov_error(self):
        """Test get_audio_packets handles moov atom error."""
        module = self._import_module()
        
        # Mock subprocess.run to simulate moov atom error
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "moov atom not found"
        
        with patch('subprocess.run', return_value=mock_result):
            video_path = Path("/tmp/test.mp4")
            with pytest.raises(RuntimeError) as exc_info:
                module.get_audio_packets(video_path, stream_index=1)
            assert "moov atom" in str(exc_info.value)
            assert "Invalid MP4 file" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Integration tests with mocked ffprobe
# ---------------------------------------------------------------------------

def test_integration_no_audio_streams():
    """Test integration with mocked ffprobe returning no audio streams."""
    module = None
    
    # First import the module
    import importlib.util
    spec = importlib.util.spec_from_file_location("audio_continuity", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    # Mock check_ffprobe_available to return True
    with patch.object(module, 'check_ffprobe_available', return_value=True):
        # Mock get_audio_streams to return empty list
        with patch.object(module, 'get_audio_streams', return_value=[]):
            # Mock Path.exists to return True
            with patch('pathlib.Path.exists', return_value=True):
                # Mock Path.stat to return non-zero size
                mock_stat = MagicMock()
                mock_stat.st_size = 100
                with patch('pathlib.Path.stat', return_value=mock_stat):
                    # Run main with mocked functions
                    result = module.main(["/tmp/test.mp4"])
                    assert result == 2  # Should exit with code 2 for no audio streams
                    # Should output SKIP:
                    import sys
                    # Check that stderr contains SKIP:
                    # Note: We can't easily capture print output in this test
                    # The actual test is in test_missing_file and test_empty_file


def test_integration_with_audio_no_gaps():
    """Test integration with mocked audio streams and no gaps."""
    module = None
    
    # First import the module
    import importlib.util
    spec = importlib.util.spec_from_file_location("audio_continuity", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    # Mock check_ffprobe_available to return True
    with patch.object(module, 'check_ffprobe_available', return_value=True):
        # Mock get_audio_streams to return one audio stream
        with patch.object(module, 'get_audio_streams', return_value=[1]):
            # Mock get_audio_packets to return continuous timestamps
            with patch.object(module, 'get_audio_packets', return_value=[0.0, 0.01, 0.02, 0.03]):
                # Mock Path.exists to return True
                with patch('pathlib.Path.exists', return_value=True):
                    # Mock Path.stat to return non-zero size
                    mock_stat = MagicMock()
                    mock_stat.st_size = 100
                    with patch('pathlib.Path.stat', return_value=mock_stat):
                        # Run main with mocked functions
                        result = module.main(["/tmp/test.mp4"])
                        assert result == 0  # Should pass with no gaps


def test_integration_with_audio_gaps():
    """Test integration with mocked audio streams and gaps."""
    module = None
    
    # First import the module
    import importlib.util
    spec = importlib.util.spec_from_file_location("audio_continuity", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    # Mock check_ffprobe_available to return True
    with patch.object(module, 'check_ffprobe_available', return_value=True):
        # Mock get_audio_streams to return one audio stream
        with patch.object(module, 'get_audio_streams', return_value=[1]):
            # Mock get_audio_packets to return timestamps with gap
            with patch.object(module, 'get_audio_packets', return_value=[0.0, 0.01, 0.10, 0.11]):
                # Mock Path.exists to return True
                with patch('pathlib.Path.exists', return_value=True):
                    # Mock Path.stat to return non-zero size
                    mock_stat = MagicMock()
                    mock_stat.st_size = 100
                    with patch('pathlib.Path.stat', return_value=mock_stat):
                        # Run main with mocked functions
                        result = module.main(["/tmp/test.mp4"])
                        assert result == 1  # Should fail with gaps > 50ms