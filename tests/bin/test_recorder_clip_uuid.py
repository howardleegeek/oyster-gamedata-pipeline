#!/usr/bin/env python3
"""Tests for bin/recorder_clip_uuid.py"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bin import recorder_clip_uuid


class TestGenerateClipUuid:
    """Tests for generate_clip_uuid function."""

    def test_generate_clip_uuid_returns_uuid4_format(self):
        """UUID should be valid UUID4 string."""
        result = recorder_clip_uuid.generate_clip_uuid()
        # Should be valid UUID4 format (8-4-4-4-12 hex chars)
        assert len(result) == 36
        assert result.count("-") == 4

    def test_generate_clip_uuid_returns_unique_values(self):
        """Each call should return a unique UUID."""
        results = {recorder_clip_uuid.generate_clip_uuid() for _ in range(100)}
        assert len(results) == 100

    def test_generate_clip_uuid_is_string(self):
        """Result should be a string."""
        result = recorder_clip_uuid.generate_clip_uuid()
        assert isinstance(result, str)


class TestSuffixFilename:
    """Tests for suffix_filename function."""

    def test_suffix_filename_simple(self):
        """video.mp4 → video-a1b2c3d4.mp4"""
        fp = Path("video.mp4")
        clip_uuid = "a1b2c3d4-e5f6-4789-a012-3456789abcdef"
        result = recorder_clip_uuid.suffix_filename(fp, clip_uuid)
        assert result == Path("video-a1b2c3d4.mp4")

    def test_suffix_filename_multiple_dots(self):
        """video.1080p.mp4 → video.1080p-a1b2c3d4.mp4"""
        fp = Path("video.1080p.mp4")
        clip_uuid = "a1b2c3d4-e5f6-4789-a012-3456789abcdef"
        result = recorder_clip_uuid.suffix_filename(fp, clip_uuid)
        assert result == Path("video.1080p-a1b2c3d4.mp4")

    def test_suffix_filename_no_extension(self):
        """file → file-a1b2c3d4 (no extension)"""
        fp = Path("file")
        clip_uuid = "a1b2c3d4-e5f6-4789-a012-3456789abcdef"
        result = recorder_clip_uuid.suffix_filename(fp, clip_uuid)
        assert result == Path("file-a1b2c3d4")

    def test_suffix_filename_uses_first_uuid_segment(self):
        """Only first hyphen-separated segment used."""
        fp = Path("clip.mp4")
        clip_uuid = "verylong-uuid-with-multiple-segments"
        result = recorder_clip_uuid.suffix_filename(fp, clip_uuid)
        assert result == Path("clip-verylong.mp4")


class TestBuildMetadata:
    """Tests for build_metadata function."""

    def test_build_metadata_basic(self):
        """Basic metadata with just clip_id and clip_uuid."""
        result = recorder_clip_uuid.build_metadata("clip_001", "abc123")
        assert result["clip_id"] == "clip_001"
        assert result["clip_uuid"] == "abc123"
        assert "created_at" in result
        assert "hostname" in result
        # ISO format should parse
        datetime.fromisoformat(result["created_at"])

    def test_build_metadata_with_filepath(self):
        """Including filepath adds filename fields."""
        fp = Path("/path/to/video.mp4")
        result = recorder_clip_uuid.build_metadata("clip_001", "abc123", filepath=fp)
        assert result["original_filename"] == "video.mp4"
        assert result["suffixed_filename"] == "video-abc123.mp4"

    def test_build_metadata_with_extra(self):
        """Extra dict gets merged in."""
        extra = {"session_id": "sess_123", "duration_seconds": 120}
        result = recorder_clip_uuid.build_metadata(
            "clip_001", "abc123", extra=extra
        )
        assert result["session_id"] == "sess_123"
        assert result["duration_seconds"] == 120

    def test_build_metadata_file_size_when_exists(self, tmp_path):
        """File size included when file exists."""
        test_file = tmp_path / "video.mp4"
        test_file.write_text("dummy content")
        result = recorder_clip_uuid.build_metadata("clip_001", "abc123", filepath=test_file)
        assert result["file_size_bytes"] == len(b"dummy content")

    def test_build_metadata_file_size_none_when_missing(self, tmp_path):
        """File size None when file doesn't exist."""
        test_file = tmp_path / "nonexistent.mp4"
        result = recorder_clip_uuid.build_metadata("clip_001", "abc123", filepath=test_file)
        assert result["file_size_bytes"] is None


class TestInitDb:
    """Tests for init_db function."""

    def test_init_db_creates_table(self, tmp_path):
        """Creates database with clip_uuids table."""
        db_path = tmp_path / "test.db"
        conn = recorder_clip_uuid.init_db(db_path)
        # Table should exist
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='clip_uuids'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_init_db_idempotent(self, tmp_path):
        """Calling init_db twice doesn't fail."""
        db_path = tmp_path / "test.db"
        conn1 = recorder_clip_uuid.init_db(db_path)
        conn1.close()
        conn2 = recorder_clip_uuid.init_db(db_path)
        conn2.close()
        # Should succeed

    def test_init_db_returns_connection(self, tmp_path):
        """Returns sqlite3 connection object."""
        db_path = tmp_path / "test.db"
        conn = recorder_clip_uuid.init_db(db_path)
        assert isinstance(conn, sqlite3.Connection)
        conn.close()


class TestInsertClipRecord:
    """Tests for insert_clip_record function."""

    def test_insert_clip_record_basic(self, tmp_path):
        """Inserts a clip record."""
        db_path = tmp_path / "test.db"
        conn = recorder_clip_uuid.init_db(db_path)
        recorder_clip_uuid.insert_clip_record(
            conn, "clip_001", "uuid_123", "testhost", "2024-01-01T00:00:00Z"
        )
        cursor = conn.execute("SELECT * FROM clip_uuids WHERE clip_id=?", ("clip_001",))
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "clip_001"
        assert row[1] == "uuid_123"
        assert row[2] == "testhost"
        conn.close()

    def test_insert_clip_record_upsert(self, tmp_path):
        """Upserts on conflict (same clip_id)."""
        db_path = tmp_path / "test.db"
        conn = recorder_clip_uuid.init_db(db_path)
        recorder_clip_uuid.insert_clip_record(
            conn, "clip_001", "uuid_123", "host1", "2024-01-01T00:00:00Z"
        )
        recorder_clip_uuid.insert_clip_record(
            conn, "clip_001", "uuid_456", "host2", "2024-01-02T00:00:00Z"
        )
        cursor = conn.execute("SELECT clip_uuid, hostname FROM clip_uuids WHERE clip_id=?", ("clip_001",))
        row = cursor.fetchone()
        # Updated to new values
        assert row[0] == "uuid_456"
        assert row[1] == "host2"
        conn.close()

    def test_insert_clip_record_with_filename(self, tmp_path):
        """Stores filename when provided."""
        db_path = tmp_path / "test.db"
        conn = recorder_clip_uuid.init_db(db_path)
        recorder_clip_uuid.insert_clip_record(
            conn, "clip_001", "uuid_123", "host", "2024-01-01T00:00:00Z", "video.mp4"
        )
        cursor = conn.execute("SELECT filename FROM clip_uuids WHERE clip_id=?", ("clip_001",))
        row = cursor.fetchone()
        assert row[0] == "video.mp4"
        conn.close()


class TestBuildParser:
    """Tests for build_parser function."""

    def test_build_parser_has_clip_dir(self):
        """Parser accepts --clip-dir."""
        parser = recorder_clip_uuid.build_parser()
        args = parser.parse_args(["--clip-dir", "/path/to/clips"])
        assert args.clip_dir == Path("/path/to/clips")

    def test_build_parser_has_clip_id(self):
        """Parser accepts --clip-id."""
        parser = recorder_clip_uuid.build_parser()
        args = parser.parse_args(["--clip-id", "clip_001"])
        assert args.clip_id == "clip_001"

    def test_build_parser_has_output_json(self):
        """Parser accepts --output-json."""
        parser = recorder_clip_uuid.build_parser()
        args = parser.parse_args(["--output-json", "meta.json"])
        assert args.output_json == Path("meta.json")

    def test_build_parser_has_dry_run(self):
        """Parser accepts --dry-run."""
        parser = recorder_clip_uuid.build_parser()
        args = parser.parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_build_parser_has_verbose(self):
        """Parser accepts -v/--verbose."""
        parser = recorder_clip_uuid.build_parser()
        args = parser.parse_args(["-v"])
        assert args.verbose is True


class TestMain:
    """Tests for main function."""

    def test_main_clip_id_writes_json(self, tmp_path):
        """Single clip_id writes JSON output."""
        output = tmp_path / "meta.json"
        with patch.object(recorder_clip_uuid, "init_db") as mock_init:
            mock_conn = sqlite3.connect(":memory:")
            mock_init.return_value = mock_conn
            result = recorder_clip_uuid.main([
                "--clip-id", "clip_001",
                "--output-json", str(output),
                "--dry-run"
            ])
        assert result == 0
        data = json.loads(output.read_text())
        assert len(data) == 1
        assert data[0]["clip_id"] == "clip_001"
        assert "clip_uuid" in data[0]
        mock_conn.close()

    def test_main_clip_id_dry_run_no_db(self, tmp_path):
        """Dry run doesn't call init_db."""
        with patch.object(recorder_clip_uuid, "init_db") as mock_init:
            result = recorder_clip_uuid.main(["--clip-id", "clip_001", "--dry-run"])
        mock_init.assert_not_called()
        assert result == 0

    def test_main_clip_dir_not_directory(self, tmp_path):
        """Error when --clip-dir is not a directory."""
        not_dir = tmp_path / "not_a_dir"
        result = recorder_clip_uuid.main(["--clip-dir", str(not_dir)])
        assert result == 1

    def test_main_clip_dir_processes_files(self, tmp_path):
        """Processes files in directory."""
        clip_dir = tmp_path / "clips"
        clip_dir.mkdir()
        (clip_dir / "video1.mp4").write_text("content1")
        (clip_dir / "video2.mp4").write_text("content2")
        output = tmp_path / "meta.json"

        with patch.object(recorder_clip_uuid, "init_db") as mock_init:
            mock_conn = sqlite3.connect(":memory:")
            mock_init.return_value = mock_conn
            with patch.object(recorder_clip_uuid, "insert_clip_record"):
                result = recorder_clip_uuid.main([
                    "--clip-dir", str(clip_dir),
                    "--output-json", str(output),
                    "--dry-run"
                ])
        assert result == 0
        data = json.loads(output.read_text())
        assert len(data) == 2
        mock_conn.close()

    def test_main_clip_dir_skips_non_files(self, tmp_path):
        """Skips subdirectories in clip-dir."""
        clip_dir = tmp_path / "clips"
        clip_dir.mkdir()
        (clip_dir / "video.mp4").write_text("content")
        subdir = clip_dir / "subdir"
        subdir.mkdir()

        output = tmp_path / "meta.json"
        with patch.object(recorder_clip_uuid, "init_db") as mock_init:
            mock_conn = sqlite3.connect(":memory:")
            mock_init.return_value = mock_conn
            with patch.object(recorder_clip_uuid, "insert_clip_record"):
                result = recorder_clip_uuid.main([
                    "--clip-dir", str(clip_dir),
                    "--output-json", str(output),
                    "--dry-run"
                ])
        data = json.loads(output.read_text())
        # Only 1 file processed, subdir ignored
        assert len(data) == 1
        mock_conn.close()

    def test_main_clip_dir_skips_db_file(self, tmp_path):
        """Skips the DB file if it's in the clip directory."""
        clip_dir = tmp_path / "clips"
        clip_dir.mkdir()
        (clip_dir / "video.mp4").write_text("content")
        db_path = clip_dir / "systeminfo.db"
        db_path.write_text("db content")
        output = tmp_path / "meta.json"

        with patch.object(recorder_clip_uuid, "init_db") as mock_init:
            mock_conn = sqlite3.connect(":memory:")
            mock_init.return_value = mock_conn
            with patch.object(recorder_clip_uuid, "insert_clip_record"):
                result = recorder_clip_uuid.main([
                    "--clip-dir", str(clip_dir),
                    "--db-path", str(db_path),
                    "--output-json", str(output),
                    "--dry-run"
                ])
        data = json.loads(output.read_text())
        # Only 1 file (video.mp4), db file skipped
        assert len(data) == 1
        mock_conn.close()

    def test_main_no_args_shows_help(self):
        """No arguments shows help and returns 1."""
        with patch("sys.stdout") as mock_stdout:
            result = recorder_clip_uuid.main([])
        assert result == 1

    def test_main_clip_id_without_clip_dir(self, tmp_path):
        """clip-id without clip-dir works (new UUID generation)."""
        output = tmp_path / "meta.json"
        with patch.object(recorder_clip_uuid, "init_db") as mock_init:
            mock_conn = sqlite3.connect(":memory:")
            mock_init.return_value = mock_conn
            result = recorder_clip_uuid.main([
                "--clip-id", "clip_001",
                "--output-json", str(output),
                "--dry-run"
            ])
        assert result == 0
        data = json.loads(output.read_text())
        assert "clip_uuid" in data[0]
        mock_conn.close()

    def test_main_output_json_notWritten_when_no_results(self):
        """No output file when no results and --output-json provided."""
        with patch("sys.stdout") as mock_stdout:
            result = recorder_clip_uuid.main([])
        assert result == 1
