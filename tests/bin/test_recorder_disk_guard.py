#!/usr/bin/env python3
"""
Tests for bin/recorder_disk_guard.py — Pre-flight free-space check (G272, W31).

Purpose:
A 5-minute 1080p H.265 clip plus depth EXR sidecars is ~250–400 MB. If the
tester's ``Documents`` folder is too full, ffmpeg silently truncates and
the clip fails QA. This guard runs *before* ``ffmpeg`` is spawned and
refuses to start when free space is below ``MIN_FREE_BYTES`` (default
500 MB).

Test coverage:
- documents_dir (finds Documents, documents, falls back to home)
- free_bytes (cross-platform resolution, returns free space)
- ensure_disk_space (above threshold passes, below threshold raises DiskGuardError)
- _main CLI (no args uses default, custom path, exit codes)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))
from bin.recorder_disk_guard import (
    DiskGuardError,
    _main,
    documents_dir,
    ensure_disk_space,
    free_bytes,
)


class TestDocumentsDir:
    """Tests for documents_dir function."""

    @patch("bin.recorder_disk_guard.Path")
    def test_finds_documents_uppercase(self, mock_path_class):
        """Test that Documents (uppercase) is found."""
        # Create a mock for Path.home()
        mock_home = MagicMock()
        mock_home.__truediv__ = lambda self, x: MagicMock(is_dir=lambda: str(x) == "Documents")
        mock_path_class.home.return_value = mock_home
        
        result = documents_dir()
        
        # Should have called home() and checked is_dir()
        assert mock_path_class.home.called

    @patch("bin.recorder_disk_guard.Path")
    def test_falls_back_to_home(self, mock_path_class):
        """Test that home is returned if no Documents folder exists."""
        # Create a mock for Path.home() that returns paths where is_dir() always returns False
        mock_home = MagicMock()
        mock_path_obj = MagicMock()
        mock_path_obj.is_dir.return_value = False
        mock_home.__truediv__ = lambda self, x: mock_path_obj
        mock_path_class.home.return_value = mock_home
        
        result = documents_dir()
        
        # Should have called home()
        assert mock_path_class.home.called


class TestFreeBytes:
    """Tests for free_bytes function."""

    @patch("bin.recorder_disk_guard.shutil.disk_usage")
    def test_returns_free_space_from_disk_usage(self, mock_disk_usage):
        """Test that free_bytes returns the free space from disk_usage."""
        # Simulate 1 GB free
        mock_disk_usage.return_value = MagicMock(free=1024**3)
        
        result = free_bytes()
        
        assert result == 1024**3
        mock_disk_usage.assert_called_once()

    @patch("bin.recorder_disk_guard.shutil.disk_usage")
    def test_uses_custom_target_path(self, mock_disk_usage):
        """Test that free_bytes uses the provided target path."""
        custom_path = MagicMock(spec=Path)
        custom_path.__str__ = lambda self: "/custom/target"
        custom_path.exists.return_value = True
        mock_disk_usage.return_value = MagicMock(free=500 * 1024**2)
        
        result = free_bytes(custom_path)
        
        mock_disk_usage.assert_called_once()
        assert result == 500 * 1024**2


class TestEnsureDiskSpace:
    """Tests for ensure_disk_space function."""

    @patch("bin.recorder_disk_guard.free_bytes")
    def test_passes_when_free_above_threshold(self, mock_free_bytes):
        """Test that ensure_disk_space passes when free space is above threshold."""
        # 1 GB free, threshold is 500 MB
        mock_free_bytes.return_value = 1024 * 1024 * 1024
        
        result = ensure_disk_space()
        
        assert result == 1024 * 1024 * 1024
        mock_free_bytes.assert_called_once()

    @patch("bin.recorder_disk_guard.free_bytes")
    def test_raises_when_free_below_threshold(self, mock_free_bytes):
        """Test that ensure_disk_space raises DiskGuardError when below threshold."""
        # 100 MB free, threshold is 500 MB
        mock_free_bytes.return_value = 100 * 1024 * 1024
        
        with pytest.raises(DiskGuardError) as exc_info:
            ensure_disk_space()
        
        # Error message should contain the values in MB
        error_msg = str(exc_info.value)
        assert "100" in error_msg
        assert "500" in error_msg

    @patch("bin.recorder_disk_guard.free_bytes")
    def test_passes_when_free_exactly_at_threshold(self, mock_free_bytes):
        """Test that ensure_disk_space passes when free is exactly at threshold (uses < not <=)."""
        # Exactly 500 MB free (threshold). Implementation uses < so this should pass.
        mock_free_bytes.return_value = 500 * 1024 * 1024
        
        # Should NOT raise - implementation uses < not <=
        result = ensure_disk_space()
        assert result == 500 * 1024 * 1024

    @patch("bin.recorder_disk_guard.free_bytes")
    def test_custom_min_free_bytes(self, mock_free_bytes):
        """Test that custom min_free_bytes parameter is respected."""
        # 600 MB free, custom threshold 500 MB - should pass
        mock_free_bytes.return_value = 600 * 1024 * 1024
        
        result = ensure_disk_space(min_free_bytes=500 * 1024 * 1024)
        
        assert result == 600 * 1024 * 1024

    @patch("bin.recorder_disk_guard.free_bytes")
    def test_custom_target_and_threshold(self, mock_free_bytes):
        """Test custom target path with custom threshold."""
        custom_path = MagicMock(spec=Path)
        custom_path.exists.return_value = True
        custom_threshold = 100 * 1024 * 1024  # 100 MB
        mock_free_bytes.return_value = 200 * 1024 * 1024  # 200 MB
        
        result = ensure_disk_space(target=custom_path, min_free_bytes=custom_threshold)
        
        mock_free_bytes.assert_called_once_with(custom_path)
        assert result == 200 * 1024 * 1024


class TestMain:
    """Tests for _main CLI function."""

    @patch("bin.recorder_disk_guard.ensure_disk_space")
    def test_main_passes_when_space_available(self, mock_ensure):
        """Test that _main returns 0 when disk space is available."""
        mock_ensure.return_value = 2 * 1024 * 1024 * 1024  # 2 GB
        
        result = _main([])
        
        assert result == 0

    @patch("bin.recorder_disk_guard.ensure_disk_space")
    def test_main_fails_when_space_insufficient(self, mock_ensure):
        """Test that _main returns 1 when disk space is insufficient."""
        mock_ensure.side_effect = DiskGuardError("磁盘剩余 100 MB 太少 — 至少需要 500MB")
        
        result = _main([])
        
        assert result == 1

    @patch("bin.recorder_disk_guard.ensure_disk_space")
    @patch("bin.recorder_disk_guard.documents_dir")
    def test_main_uses_custom_path_argument(self, mock_docs_dir, mock_ensure):
        """Test that _main uses custom path from argv."""
        mock_ensure.return_value = 2 * 1024 * 1024 * 1024
        
        result = _main(["/custom/path"])
        
        mock_ensure.assert_called_once()

    @patch("bin.recorder_disk_guard.ensure_disk_space")
    @patch("bin.recorder_disk_guard.documents_dir")
    def test_main_uses_default_when_no_args(self, mock_docs_dir, mock_ensure):
        """Test that _main uses default documents_dir when no args provided."""
        mock_ensure.return_value = 2 * 1024 * 1024 * 1024
        mock_docs_dir.return_value = Path("/default/docs")
        
        result = _main([])
        
        mock_ensure.assert_called_once()

    @patch("bin.recorder_disk_guard.ensure_disk_space")
    def test_main_error_message_written_to_stderr(self, mock_ensure):
        """Test that error message is written to stderr on failure."""
        mock_ensure.side_effect = DiskGuardError("磁盘剩余 100 MB 太少")
        
        with patch("sys.stderr") as mock_stderr:
            _main([])
            mock_stderr.write.assert_called_once()
            assert "100" in mock_stderr.write.call_args[0][0]
