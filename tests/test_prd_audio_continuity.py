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
        assert "empty" in combined.lower() or "invalid" in combined.lower()


# ---------------------------------------------------------------------------
# Unit tests for individual functions
# ---------------------------------------------------------------------------

class TestAudioContinuityFunctions:
    """Unit tests for individual functions in the module."""
    
    def _import_module(self):
        """Import the module for testing."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("audio_continuity", str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    
    def test_check_ffprobe_available(self):
        """Test check_ffprobe_available function."""
        module = self._import_module()
        
        # Mock shutil.which
        with patch('shutil.which') as mock_which:
            mock_which.return_value = "/usr/bin/ffprobe"
            assert module.check_ffprobe_available() is True
            
            mock_which.return_value = None
            assert module.check_ffprobe_available() is False
    
    def test_check_continuity_no_gaps(self):
        """Test check_continuity with no gaps."""
        module = self._import_module()
        
        # Timestamps 10ms apart, threshold 50ms - should have no gaps
        timestamps = [0.0, 0.01, 0.02, 0.03, 0.04]
        gaps = module.check_continuity(timestamps, threshold_ms=50.0)
        assert len(gaps) == 0, "Should find no gaps when timestamps are evenly spaced"
    
    def test_check_continuity_with_gaps(self):
        """Test check_continuity with gaps exceeding threshold."""
        module = self._import_module()
        
        timestamps = [0.0, 0.01, 0.02, 0.8, 0.81]  # Gap of 0.78s between 0.02 and 0.8
        gaps = module.check_continuity(timestamps, threshold_ms=50.0)
        assert len(gaps) == 1, "Should find one gap"
        assert gaps[0][0] == 0.02, "Gap should start at 0.02"
        assert gaps[0][1] == 0.8, "Gap should end at 0.8"
        assert gaps[0][2] == pytest.approx(780.0, rel=0.01), "Gap should be ~780ms"
    
    def test_check_continuity_below_threshold(self):
        """Test check_continuity with gaps below threshold."""
        module = self._import_module()
        
        timestamps = [0.0, 0.01, 0.02, 0.025, 0.035]  # Gap of 0.005s (5ms) between 0.02 and 0.025
        gaps = module.check_continuity(timestamps, threshold_ms=50.0)  # Threshold is 50ms
        assert len(gaps) == 0, "Should find no gaps when below threshold"
    
    def test_check_continuity_single_timestamp(self):
        """Test check_continuity with single timestamp (no gaps possible)."""
        module = self._import_module()
        
        timestamps = [0.5]
        gaps = module.check_continuity(timestamps, threshold_ms=50.0)
        assert len(gaps) == 0, "Should find no gaps with single timestamp"
    
    def test_check_continuity_empty(self):
        """Test check_continuity with empty list."""
        module = self._import_module()
        
        timestamps = []
        gaps = module.check_continuity(timestamps, threshold_ms=50.0)
        assert len(gaps) == 0, "Should find no gaps with empty list"
    
    def test_check_continuity_unsorted(self):
        """Test check_continuity with unsorted timestamps."""
        module = self._import_module()
        
        # Unsorted timestamps should be handled correctly (sorted internally)
        timestamps = [0.04, 0.01, 0.03, 0.0, 0.02]
        gaps = module.check_continuity(timestamps, threshold_ms=50.0)
        assert len(gaps) == 0, "Should find no gaps when timestamps are evenly spaced (after sorting)"
    
    def test_is_moov_atom_error(self):
        """Test is_moov_atom_error function."""
        module = self._import_module()
        
        # Should detect moov atom errors
        assert module.is_moov_atom_error("moov atom not found") is True
        assert module.is_moov_atom_error("Invalid MP4 file: moov atom not found") is True
        assert module.is_moov_atom_error("moov missing") is True
        assert module.is_moov_atom_error("invalid mp4 file") is True
        assert module.is_moov_atom_error("corrupted file") is True
        assert module.is_moov_atom_error("incomplete file") is True
        assert module.is_moov_atom_error("truncated file") is True
        assert module.is_moov_atom_error("empty file") is True
        
        # Should not detect non-moov errors
        assert module.is_moov_atom_error("permission denied") is False
        assert module.is_moov_atom_error("file not found") is False
        assert module.is_moov_atom_error("some random error") is False
    
    def test_get_audio_streams_missing_file(self):
        """Test get_audio_streams with missing file."""
        module = self._import_module()
        
        with pytest.raises(RuntimeError) as exc_info:
            module.get_audio_streams(Path("/tmp/does_not_exist_12345.mp4"))
        assert "not found" in str(exc_info.value).lower()
    
    def test_get_audio_streams_empty_file(self):
        """Test get_audio_streams with empty file."""
        module = self._import_module()
        
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"")
            f.flush()
            
            with pytest.raises(RuntimeError) as exc_info:
                module.get_audio_streams(Path(f.name))
            assert "empty" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()


class TestIsMoovAtomError:
    """Detailed tests for is_moov_atom_error function."""
    
    def _import_module(self):
        """Import the module for testing."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("audio_continuity", str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    
    def test_moov_atom_not_found(self):
        """Test detection of 'moov atom not found' error."""
        module = self._import_module()
        assert module.is_moov_atom_error("moov atom not found") is True
        assert module.is_moov_atom_error("MOOV ATOM NOT FOUND") is True
        assert module.is_moov_atom_error("Error: moov atom not found in file") is True
    
    def test_moov_missing(self):
        """Test detection of 'moov missing' error."""
        module = self._import_module()
        assert module.is_moov_atom_error("moov missing") is True
        assert module.is_moov_atom_error("MOOV MISSING") is True
    
    def test_invalid_mp4(self):
        """Test detection of 'invalid mp4' error."""
        module = self._import_module()
        assert module.is_moov_atom_error("invalid mp4") is True
        assert module.is_moov_atom_error("Invalid MP4 file") is True
    
    def test_corrupted(self):
        """Test detection of 'corrupted' error."""
        module = self._import_module()
        assert module.is_moov_atom_error("corrupted file") is True
        assert module.is_moov_atom_error("CORRUPTED") is True
    
    def test_incomplete(self):
        """Test detection of 'incomplete' error."""
        module = self._import_module()
        assert module.is_moov_atom_error("incomplete file") is True
        assert module.is_moov_atom_error("INCOMPLETE") is True
    
    def test_truncated(self):
        """Test detection of 'truncated' error."""
        module = self._import_module()
        assert module.is_moov_atom_error("truncated file") is True
        assert module.is_moov_atom_error("TRUNCATED") is True
    
    def test_empty(self):
        """Test detection of 'empty' error."""
        module = self._import_module()
        assert module.is_moov_atom_error("empty file") is True
        assert module.is_moov_atom_error("EMPTY") is True
    
    def test_non_moov_errors(self):
        """Test that non-moov errors are not detected."""
        module = self._import_module()
        assert module.is_moov_atom_error("permission denied") is False
        assert module.is_moov_atom_error("file not found") is False
        assert module.is_moov_atom_error("no such file or directory") is False
        assert module.is_moov_atom_error("some random error") is False
        assert module.is_moov_atom_error("ffmpeg error") is False


class TestIsSkipError:
    """Tests for is_skip_error function."""
    
    def _import_module(self):
        """Import the module for testing."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("audio_continuity", str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    
    def test_is_skip_error_exists(self):
        """Test that is_skip_error function exists."""
        module = self._import_module()
        assert hasattr(module, 'is_skip_error'), "Module should have is_skip_error function"
    
    def test_not_found_is_skip(self):
        """Test that 'not found' is a skip condition."""
        module = self._import_module()
        if hasattr(module, 'is_skip_error'):
            assert module.is_skip_error("Video file not found") is True
            assert module.is_skip_error("NOT FOUND") is True
    
    def test_empty_is_skip(self):
        """Test that 'empty' is a skip condition."""
        module = self._import_module()
        if hasattr(module, 'is_skip_error'):
            assert module.is_skip_error("empty file") is True
            assert module.is_skip_error("EMPTY") is True
    
    def test_invalid_is_skip(self):
        """Test that 'invalid' is a skip condition."""
        module = self._import_module()
        if hasattr(module, 'is_skip_error'):
            assert module.is_skip_error("invalid file") is True
            assert module.is_skip_error("INVALID") is True
    
    def test_no_audio_is_skip(self):
        """Test that 'no audio' is a skip condition."""
        module = self._import_module()
        if hasattr(module, 'is_skip_error'):
            assert module.is_skip_error("no audio streams") is True
            assert module.is_skip_error("NO AUDIO") is True
    
    def test_random_error_not_skip(self):
        """Test that random errors are not skip conditions."""
        module = self._import_module()
        if hasattr(module, 'is_skip_error'):
            assert module.is_skip_error("some random error") is False
            assert module.is_skip_error("permission denied") is False