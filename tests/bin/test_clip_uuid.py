#!/usr/bin/env python3
"""Tests for bin/clip_uuid.py."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from bin import clip_uuid


class TestNewClipUuid:
    """Tests for new_clip_uuid()."""

    def test_returns_hex_string(self):
        """new_clip_uuid returns a hexadecimal string."""
        result = clip_uuid.new_clip_uuid()
        assert isinstance(result, str)
        # Hex strings should only contain 0-9a-f
        assert all(c in "0123456789abcdef" for c in result)

    def test_length_32(self):
        """new_clip_uuid returns 32-character hex string (no dashes)."""
        result = clip_uuid.new_clip_uuid()
        assert len(result) == 32

    def test_returns_unique_values(self):
        """new_clip_uuid returns unique values on each call."""
        results = {clip_uuid.new_clip_uuid() for _ in range(100)}
        assert len(results) == 100  # All should be unique


class TestWriteMarker:
    """Tests for _write_marker()."""

    def test_creates_marker_file(self, tmp_path):
        """_write_marker creates a file with correct name pattern."""
        clip_dir = tmp_path / "clip_2026_05_05"
        clip_dir.mkdir()
        clip_uuid_val = "abc123def45678901234567890123456"
        
        result = clip_uuid._write_marker(clip_dir, clip_uuid_val)
        
        expected = clip_dir / f".clip_uuid_{clip_uuid_val}"
        assert result == expected
        assert expected.exists()

    def test_marker_file_empty(self, tmp_path):
        """Marker file has empty content (data in filename)."""
        clip_dir = tmp_path / "clip_2026_05_05"
        clip_dir.mkdir()
        clip_uuid_val = "a" * 32
        
        result = clip_uuid._write_marker(clip_dir, clip_uuid_val)
        
        assert result.read_bytes() == b""


class TestInjectUuid:
    """Tests for inject_uuid()."""

    def test_adds_uuid_to_dict(self, tmp_path):
        """inject_uuid adds clip_uuid key to systeminfo dict."""
        clip_dir = tmp_path / "clip_2026_05_05"
        clip_dir.mkdir()
        sysinfo: dict = {"hostname": "alice-pc", "os": "windows"}
        
        result = clip_uuid.inject_uuid(sysinfo, clip_dir)
        
        assert "clip_uuid" in sysinfo
        assert sysinfo["clip_uuid"] == result
        assert len(result) == 32

    def test_idempotent_when_key_exists(self, tmp_path):
        """inject_uuid preserves existing clip_uuid (idempotent)."""
        clip_dir = tmp_path / "clip_2026_05_05"
        clip_dir.mkdir()
        existing_uuid = "existing_uuid_1234567890123456"
        sysinfo: dict = {"clip_uuid": existing_uuid}
        
        result = clip_uuid.inject_uuid(sysinfo, clip_dir)
        
        assert sysinfo["clip_uuid"] == existing_uuid
        assert result == existing_uuid

    def test_uses_provided_uuid(self, tmp_path):
        """inject_uuid uses provided UUID instead of generating."""
        clip_dir = tmp_path / "clip_2026_05_05"
        clip_dir.mkdir()
        provided_uuid = "provided12345678901234567890123"
        sysinfo: dict = {}
        
        result = clip_uuid.inject_uuid(sysinfo, clip_dir, provided_uuid)
        
        assert sysinfo["clip_uuid"] == provided_uuid
        assert result == provided_uuid

    def test_raises_if_clip_dir_not_exist(self, tmp_path):
        """inject_uuid raises FileNotFoundError if clip_dir missing."""
        missing_dir = tmp_path / "nonexistent"
        sysinfo: dict = {}
        
        with pytest.raises(FileNotFoundError):
            clip_uuid.inject_uuid(sysinfo, missing_dir)

    def test_raises_if_clip_dir_not_directory(self, tmp_path):
        """inject_uuid raises NotADirectoryError if clip_dir is file."""
        not_a_dir = tmp_path / "file.txt"
        not_a_dir.write_text("not a dir")
        sysinfo: dict = {}
        
        with pytest.raises(NotADirectoryError):
            clip_uuid.inject_uuid(sysinfo, not_a_dir)

    def test_marker_file_has_correct_name(self, tmp_path):
        """Marker file in clip_dir has correct UUID in name."""
        clip_dir = tmp_path / "clip_2026_05_05"
        clip_dir.mkdir()
        sysinfo: dict = {}
        
        result = clip_uuid.inject_uuid(sysinfo, clip_dir)
        
        marker_files = list(clip_dir.glob(".clip_uuid_*"))
        assert len(marker_files) == 1
        assert marker_files[0].name == f".clip_uuid_{result}"


class TestCli:
    """Tests for _cli() entry point."""

    def test_new_command_prints_uuid(self):
        """CLI 'new' command prints a UUID."""
        result = clip_uuid._cli(["new"])
        assert result == 0

    def test_inject_command_requires_clip_dir(self):
        """CLI 'inject' fails without --clip-dir."""
        with pytest.raises(SystemExit) as exc_info:
            clip_uuid._cli(["inject", "--systeminfo", "/tmp/x.json"])
        assert exc_info.value.code == 2

    def test_inject_command_requires_systeminfo(self):
        """CLI 'inject' fails without --systeminfo."""
        with pytest.raises(SystemExit) as exc_info:
            clip_uuid._cli(["inject", "--clip-dir", "/tmp/clip"])
        assert exc_info.value.code == 2

    def test_inject_command_missing_file(self, tmp_path):
        """CLI 'inject' fails if systeminfo file doesn't exist."""
        clip_dir = tmp_path / "clip_2026_05_05"
        clip_dir.mkdir()
        sysinfo_file = tmp_path / "systeminfo.json"
        
        # CLI prints error to stderr and returns 2
        with mock.patch("sys.stderr"):
            result = clip_uuid._cli([
                "inject",
                "--clip-dir", str(clip_dir),
                "--systeminfo", str(sysinfo_file),
            ])
        
        assert result == 2

    def test_inject_command_success(self, tmp_path):
        """CLI 'inject' successfully adds UUID to systeminfo.json."""
        clip_dir = tmp_path / "clip_2026_05_05"
        clip_dir.mkdir()
        sysinfo_file = tmp_path / "systeminfo.json"
        sysinfo_file.write_text(json.dumps({"hostname": "test-pc"}))
        
        result = clip_uuid._cli([
            "inject",
            "--clip-dir", str(clip_dir),
            "--systeminfo", str(sysinfo_file),
        ])
        
        assert result == 0
        sysinfo = json.loads(sysinfo_file.read_text())
        assert "clip_uuid" in sysinfo
        assert len(sysinfo["clip_uuid"]) == 32

    def test_inject_command_with_existing_uuid(self, tmp_path):
        """CLI 'inject' preserves existing UUID with --uuid."""
        clip_dir = tmp_path / "clip_2026_05_05"
        clip_dir.mkdir()
        sysinfo_file = tmp_path / "systeminfo.json"
        sysinfo_file.write_text(json.dumps({"hostname": "test-pc"}))
        existing_uuid = "existing12345678901234567890123"
        
        result = clip_uuid._cli([
            "inject",
            "--clip-dir", str(clip_dir),
            "--systeminfo", str(sysinfo_file),
            "--uuid", existing_uuid,
        ])
        
        assert result == 0
        sysinfo = json.loads(sysinfo_file.read_text())
        assert sysinfo["clip_uuid"] == existing_uuid

    def test_unknown_command_returns_1(self):
        """CLI returns 2 for unknown command (argparse exits with 2)."""
        with pytest.raises(SystemExit) as exc_info:
            clip_uuid._cli(["unknown"])
        assert exc_info.value.code == 2
