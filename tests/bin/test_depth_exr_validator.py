#!/usr/bin/env python3
"""Tests for bin/depth_exr_validator.py — PRD depth EXR file validator."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

# Import the module under test
import depth_exr_validator as validator


class TestCheckMagicByte:
    """Tests for check_magic_byte function."""

    def test_valid_exr_magic_bytes(self, tmp_path):
        """Valid EXR file with correct magic bytes passes."""
        test_file = tmp_path / "test.exr"
        test_file.write_bytes(b"\x76\x2f\x31\x01" + b"\x00" * 100)
        assert validator.check_magic_byte(test_file) is True

    def test_invalid_magic_bytes(self, tmp_path):
        """File with wrong magic bytes fails."""
        test_file = tmp_path / "test.exr"
        test_file.write_bytes(b"\x00\x00\x00\x00" + b"\x00" * 100)
        assert validator.check_magic_byte(test_file) is False

    def test_partial_magic_bytes(self, tmp_path):
        """File with only 2 bytes returns False."""
        test_file = tmp_path / "test.exr"
        test_file.write_bytes(b"\x76\x2f")
        assert validator.check_magic_byte(test_file) is False

    def test_file_not_found_returns_false(self):
        """Non-existent file returns False."""
        assert validator.check_magic_byte(Path("/nonexistent/file.exr")) is False

    def test_jpg_magic_bytes_not_valid_exr(self, tmp_path):
        """JPEG file is not a valid EXR."""
        test_file = tmp_path / "test.exr"
        test_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        assert validator.check_magic_byte(test_file) is False


class TestCheckStructural:
    """Tests for check_structural function.

    Note: OpenEXR is available on this machine, so we mock sys.modules to
    simulate OpenEXR not being available. This ensures tests run consistently.
    """

    def test_returns_true_when_openexr_not_available(self, tmp_path, monkeypatch):
        """When OpenEXR is not available, structural check returns True (skipped)."""
        # Mock OpenEXR as unavailable
        monkeypatch.setitem(sys.modules, "OpenEXR", None)

        test_file = tmp_path / "test.exr"
        test_file.write_bytes(b"\x76\x2f\x31\x01")
        # Need to reimport to pick up the mocked module
        import importlib
        importlib.reload(validator)
        result = validator.check_structural(test_file)
        assert result is True


class TestValidateExrFiles:
    """Tests for validate_exr_files function.

    Since OpenEXR is available on the test machine, we mock check_structural
    to avoid needing real EXR files for most tests.
    """

    def test_empty_directory_returns_zero_counts(self, tmp_path):
        """Empty depth directory returns zero counts."""
        result = validator.validate_exr_files(tmp_path)
        assert result["total"] == 0
        assert result["valid"] == 0
        assert result["invalid_files"] == []

    def test_nonexistent_directory_exits(self, tmp_path):
        """Non-existent directory causes sys.exit(1)."""
        nonexistent = tmp_path / "nonexistent"
        with pytest.raises(SystemExit) as exc_info:
            validator.validate_exr_files(nonexistent)
        assert exc_info.value.code == 1

    @patch.object(validator, "check_structural", return_value=True)
    def test_single_valid_exr_file(self, mock_structural, tmp_path):
        """Single valid EXR file is counted as valid."""
        test_file = tmp_path / "depth_0000.exr"
        test_file.write_bytes(b"\x76\x2f\x31\x01" + b"\x00" * 100)

        result = validator.validate_exr_files(tmp_path)
        assert result["total"] == 1
        assert result["valid"] == 1
        assert result["invalid_files"] == []

    @patch.object(validator, "check_structural", return_value=True)
    def test_multiple_valid_exr_files(self, mock_structural, tmp_path):
        """Multiple valid EXR files are all counted."""
        for i in range(5):
            test_file = tmp_path / f"depth_{i:04d}.exr"
            test_file.write_bytes(b"\x76\x2f\x31\x01" + b"\x00" * 100)

        result = validator.validate_exr_files(tmp_path)
        assert result["total"] == 5
        assert result["valid"] == 5
        assert result["invalid_files"] == []

    @patch.object(validator, "check_structural", return_value=True)
    def test_mixed_valid_invalid_files(self, mock_structural, tmp_path):
        """Mixed valid and invalid files are correctly counted."""
        # Create valid files
        for i in [0, 2, 4]:
            test_file = tmp_path / f"depth_{i:04d}.exr"
            test_file.write_bytes(b"\x76\x2f\x31\x01" + b"\x00" * 100)

        # Create invalid files
        for i in [1, 3]:
            test_file = tmp_path / f"depth_{i:04d}.exr"
            test_file.write_bytes(b"\x00\x00\x00\x00" + b"\x00" * 100)

        result = validator.validate_exr_files(tmp_path)
        assert result["total"] == 5
        assert result["valid"] == 3
        assert len(result["invalid_files"]) == 2

    @patch.object(validator, "check_structural", return_value=True)
    def test_subdirectory_exr_files_included(self, mock_structural, tmp_path):
        """EXR files in subdirectories are validated."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        for i in range(3):
            test_file = subdir / f"depth_{i:04d}.exr"
            test_file.write_bytes(b"\x76\x2f\x31\x01" + b"\x00" * 100)

        result = validator.validate_exr_files(tmp_path)
        assert result["total"] == 3
        assert result["valid"] == 3

    @patch.object(validator, "check_structural", return_value=False)
    def test_structural_check_failure_marks_invalid(self, mock_structural, tmp_path):
        """File with valid magic bytes but failed structural check is marked invalid."""
        test_file = tmp_path / "depth_0000.exr"
        test_file.write_bytes(b"\x76\x2f\x31\x01" + b"\x00" * 100)

        result = validator.validate_exr_files(tmp_path)
        assert result["total"] == 1
        assert result["valid"] == 0
        assert len(result["invalid_files"]) == 1


class TestMain:
    """Tests for main CLI function."""

    def test_main_exit_code_zero_when_valid(self, tmp_path, capsys):
        """Exit code 0 when all files valid."""
        # Create a valid EXR file
        test_file = tmp_path / "depth_0000.exr"
        test_file.write_bytes(b"\x76\x2f\x31\x01" + b"\x00" * 100)

        with patch.object(validator, "check_structural", return_value=True):
            with patch.object(sys, "argv", ["depth_exr_validator.py", "--depth-dir", str(tmp_path)]):
                with pytest.raises(SystemExit) as exc_info:
                    validator.main()
                assert exc_info.value.code == 0

    def test_main_exit_code_one_when_invalid(self, tmp_path, capsys):
        """Exit code 1 when any file invalid."""
        # Create an invalid EXR file (wrong magic bytes)
        test_file = tmp_path / "depth_0000.exr"
        test_file.write_bytes(b"\x00\x00\x00\x00" + b"\x00" * 100)

        with patch.object(sys, "argv", ["depth_exr_validator.py", "--depth-dir", str(tmp_path)]):
            with pytest.raises(SystemExit) as exc_info:
                validator.main()
            assert exc_info.value.code == 1

    def test_main_requires_depth_dir_argument(self, capsys):
        """Missing --depth-dir argument shows usage and exits."""
        with patch.object(sys, "argv", ["depth_exr_validator.py"]):
            with pytest.raises(SystemExit) as exc_info:
                validator.main()
            # Should exit with error (2) for missing required argument
            assert exc_info.value.code == 2
