#!/usr/bin/env python3
"""Tests for bin/clip_uuid.py — per-clip UUID4 generator + injection helpers."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parents[2] / "bin"))
import clip_uuid


class TestNewClipUuid:
    """Tests for new_clip_uuid function."""

    def test_returns_32_char_hex_string(self):
        """new_clip_uuid should return a 32-character hex string."""
        result = clip_uuid.new_clip_uuid()
        assert isinstance(result, str)
        assert len(result) == 32
        assert re.fullmatch(r"[0-9a-f]{32}", result) is not None

    def test_returns_unique_values(self):
        """Each call should return a unique UUID."""
        results = {clip_uuid.new_clip_uuid() for _ in range(100)}
        # All should be unique (set preserves uniqueness)
        assert len(results) == 100

    def test_is_valid_uuid4_format(self):
        """The hex string should be a valid UUID4 format (with dashes for full UUID)."""
        result = clip_uuid.new_clip_uuid()
        # UUID4 has specific bits: version 4 (bits 12-15 = 4), variant 1 (bits 17-19 = 2)
        # Insert dashes to get standard UUID format: 8-4-4-4-12
        full_uuid = f"{result[0:8]}-{result[8:12]}-{result[12:16]}-{result[16:20]}-{result[20:32]}"
        # Check version (4) and variant (8, 9, A, or B)
        version = int(full_uuid[14], 16)
        variant = int(full_uuid[19], 16)
        assert version == 4
        assert variant >= 8 and variant <= 11


class TestWriteMarker:
    """Tests for _write_marker function."""

    def test_creates_marker_with_correct_prefix(self, tmp_path):
        """_write_marker should create a file with correct prefix."""
        clip_uuid_str = "abc123def45678901234567890123456"
        result = clip_uuid._write_marker(tmp_path, clip_uuid_str)
        
        expected_name = f".clip_uuid_{clip_uuid_str}"
        assert result.name == expected_name
        assert result.exists()
        assert result.read_bytes() == b""

    def test_marker_path_returns_correct_path(self, tmp_path):
        """_write_marker should return the Path to the created marker."""
        clip_uuid_str = "xyz000abc111"
        result = clip_uuid._write_marker(tmp_path, clip_uuid_str)
        
        assert result.parent == tmp_path
        assert result.name == f".clip_uuid_{clip_uuid_str}"


class TestInjectUuid:
    """Tests for inject_uuid function."""

    def test_adds_uuid_to_systeminfo_dict(self, tmp_path):
        """inject_uuid should add clip_uuid key to the dict."""
        clip_dir = tmp_path / "clip_001"
        clip_dir.mkdir()
        systeminfo = {"hostname": "alice-pc"}
        
        result = clip_uuid.inject_uuid(systeminfo, clip_dir)
        
        assert "clip_uuid" in systeminfo
        assert systeminfo["clip_uuid"] == result
        assert len(result) == 32

    def test_creates_marker_file(self, tmp_path):
        """inject_uuid should create marker file in clip_dir."""
        clip_dir = tmp_path / "clip_002"
        clip_dir.mkdir()
        systeminfo = {}
        
        result = clip_uuid.inject_uuid(systeminfo, clip_dir, clip_uuid="f" * 32)
        
        marker_file = clip_dir / f".clip_uuid_{'f' * 32}"
        assert marker_file.exists()

    def test_idempotent_preserves_existing_uuid(self, tmp_path):
        """inject_uuid should preserve existing clip_uuid (idempotent)."""
        clip_dir = tmp_path / "clip_003"
        clip_dir.mkdir()
        existing_uuid = "1234567890abcdef1234567890abcdef"
        systeminfo = {"clip_uuid": existing_uuid}
        
        result = clip_uuid.inject_uuid(systeminfo, clip_dir)
        
        assert result == existing_uuid
        assert systeminfo["clip_uuid"] == existing_uuid

    def test_idempotent_with_non_string_value(self, tmp_path):
        """inject_uuid should generate new UUID if existing value is not a string."""
        clip_dir = tmp_path / "clip_004"
        clip_dir.mkdir()
        systeminfo = {"clip_uuid": 12345}  # Non-string
        
        result = clip_uuid.inject_uuid(systeminfo, clip_dir)
        
        assert isinstance(result, str)
        assert len(result) == 32

    def test_idempotent_with_empty_string(self, tmp_path):
        """inject_uuid should generate new UUID if existing value is empty string."""
        clip_dir = tmp_path / "clip_005"
        clip_dir.mkdir()
        systeminfo = {"clip_uuid": ""}
        
        result = clip_uuid.inject_uuid(systeminfo, clip_dir)
        
        assert isinstance(result, str)
        assert len(result) == 32

    def test_raises_file_not_found_for_missing_dir(self):
        """inject_uuid should raise FileNotFoundError if clip_dir doesn't exist."""
        systeminfo = {}
        nonexistent = Path("/tmp/this_definitely_does_not_exist_12345")
        
        with pytest.raises(FileNotFoundError):
            clip_uuid.inject_uuid(systeminfo, nonexistent)

    def test_raises_not_a_directory_for_file(self, tmp_path):
        """inject_uuid should raise NotADirectoryError if clip_dir is a file."""
        clip_dir = tmp_path / "file_not_dir"
        clip_dir.write_text("not a dir")
        systeminfo = {}
        
        with pytest.raises(NotADirectoryError):
            clip_uuid.inject_uuid(systeminfo, clip_dir)

    def test_custom_uuid_parameter(self, tmp_path):
        """inject_uuid should use provided UUID if given."""
        clip_dir = tmp_path / "clip_006"
        clip_dir.mkdir()
        custom_uuid = "customuuid123456789012345678901234"
        systeminfo = {}
        
        result = clip_uuid.inject_uuid(systeminfo, clip_dir, clip_uuid=custom_uuid)
        
        assert result == custom_uuid
        assert systeminfo["clip_uuid"] == custom_uuid


class TestCli:
    """Tests for CLI interface."""

    def test_new_command_prints_uuid(self):
        """CLI 'new' command should print a valid UUID."""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parents[2] / "bin" / "clip_uuid.py"), "new"],
            capture_output=True,
            text=True,
        )
        
        assert result.returncode == 0
        output = result.stdout.strip()
        assert len(output) == 32
        assert re.fullmatch(r"[0-9a-f]{32}", output) is not None

    def test_inject_command_with_valid_inputs(self, tmp_path):
        """CLI 'inject' command should work with valid inputs."""
        clip_dir = tmp_path / "clip_007"
        clip_dir.mkdir()
        systeminfo_path = tmp_path / "systeminfo.json"
        systeminfo_path.write_text(json.dumps({"hostname": "test-pc"}))
        
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parents[2] / "bin" / "clip_uuid.py"),
                "inject",
                "--clip-dir", str(clip_dir),
                "--systeminfo", str(systeminfo_path),
            ],
            capture_output=True,
            text=True,
        )
        
        assert result.returncode == 0
        
        # Check output contains UUID
        output = result.stdout.strip()
        assert len(output) == 32
        
        # Check systeminfo was updated
        systeminfo = json.loads(systeminfo_path.read_text())
        assert "clip_uuid" in systeminfo
        assert systeminfo["clip_uuid"] == output
        
        # Check marker file exists
        marker_file = clip_dir / f".clip_uuid_{output}"
        assert marker_file.exists()

    def test_inject_command_with_custom_uuid(self, tmp_path):
        """CLI 'inject' command should use provided --uuid."""
        clip_dir = tmp_path / "clip_008"
        clip_dir.mkdir()
        systeminfo_path = tmp_path / "systeminfo.json"
        systeminfo_path.write_text(json.dumps({}))
        custom_uuid = "customuuid12345678901234567890"
        
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parents[2] / "bin" / "clip_uuid.py"),
                "inject",
                "--clip-dir", str(clip_dir),
                "--systeminfo", str(systeminfo_path),
                "--uuid", custom_uuid,
            ],
            capture_output=True,
            text=True,
        )
        
        assert result.returncode == 0
        assert result.stdout.strip() == custom_uuid
        
        systeminfo = json.loads(systeminfo_path.read_text())
        assert systeminfo["clip_uuid"] == custom_uuid

    def test_inject_command_missing_systeminfo_file(self, tmp_path):
        """CLI 'inject' should return error 2 if systeminfo file missing."""
        clip_dir = tmp_path / "clip_009"
        clip_dir.mkdir()
        systeminfo_path = tmp_path / "nonexistent.json"
        
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parents[2] / "bin" / "clip_uuid.py"),
                "inject",
                "--clip-dir", str(clip_dir),
                "--systeminfo", str(systeminfo_path),
            ],
            capture_output=True,
            text=True,
        )
        
        assert result.returncode == 2
        assert "missing" in result.stderr.lower()

    def test_inject_command_invalid_json(self, tmp_path):
        """CLI 'inject' should return error if systeminfo is not valid JSON."""
        clip_dir = tmp_path / "clip_010"
        clip_dir.mkdir()
        systeminfo_path = tmp_path / "systeminfo.json"
        systeminfo_path.write_text("not valid json")
        
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parents[2] / "bin" / "clip_uuid.py"),
                "inject",
                "--clip-dir", str(clip_dir),
                "--systeminfo", str(systeminfo_path),
            ],
            capture_output=True,
            text=True,
        )
        
        # Invalid JSON causes an error (returncode != 0)
        assert result.returncode != 0

    def test_unknown_command_returns_error(self):
        """CLI should return error for unknown command."""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parents[2] / "bin" / "clip_uuid.py"), "unknown"],
            capture_output=True,
            text=True,
        )
        
        assert result.returncode == 2

    def test_no_command_shows_help(self):
        """CLI with no command should show help."""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parents[2] / "bin" / "clip_uuid.py")],
            capture_output=True,
            text=True,
        )
        
        assert result.returncode == 2
