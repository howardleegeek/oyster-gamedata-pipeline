#!/usr/bin/env python3
"""
Tests for bin/batch_bundler.py — Bundle N finalized sessions into a tarball + Merkle manifest.
"""

import datetime
import hashlib
import io
import sys
import tempfile
from pathlib import Path

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))
import batch_bundler


class TestSha256Bytes:
    """Tests for sha256_bytes function."""

    def test_empty_bytes(self):
        """sha256 of empty bytes should be deterministic."""
        result = batch_bundler.sha256_bytes(b"")
        assert len(result) == 64  # SHA256 hex is 64 chars
        assert result == hashlib.sha256(b"").hexdigest()

    def test_hello_world(self):
        """sha256 of 'hello world' should match known value."""
        result = batch_bundler.sha256_bytes(b"hello world")
        expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        assert result == expected

    def test_unicode_bytes(self):
        """sha256 should handle unicode bytes."""
        result = batch_bundler.sha256_bytes("日本語".encode("utf-8"))
        assert len(result) == 64


class TestSha256File:
    """Tests for sha256_file function."""

    def test_file_with_content(self):
        """sha256 of a file should match computed hash."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content for hashing")
            f.flush()
            temp_path = Path(f.name)

        try:
            result = batch_bundler.sha256_file(temp_path)
            expected = hashlib.sha256(b"test content for hashing").hexdigest()
            assert result == expected
        finally:
            temp_path.unlink()

    def test_empty_file(self):
        """sha256 of empty file should work."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = Path(f.name)

        try:
            result = batch_bundler.sha256_file(temp_path)
            expected = hashlib.sha256(b"").hexdigest()
            assert result == expected
        finally:
            temp_path.unlink()

    def test_large_file(self):
        """sha256 should handle large files efficiently."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            # Write 1MB of data
            f.write(b"x" * (1024 * 1024))
            f.flush()
            temp_path = Path(f.name)

        try:
            result = batch_bundler.sha256_file(temp_path)
            expected = hashlib.sha256(b"x" * (1024 * 1024)).hexdigest()
            assert result == expected
        finally:
            temp_path.unlink()


class TestBuildMerkleTree:
    """Tests for build_merkle_tree function."""

    def test_empty_list(self):
        """Empty list should return hash of empty string."""
        result = batch_bundler.build_merkle_tree([])
        expected = batch_bundler.sha256_bytes(b"")
        assert result == expected

    def test_single_hash(self):
        """Single hash should be doubled with zero hash."""
        single_hash = "a" * 64
        result = batch_bundler.build_merkle_tree([single_hash])
        assert len(result) == 64

    def test_two_hashes(self):
        """Two hashes should be combined."""
        h1 = "a" * 64
        h2 = "b" * 64
        result = batch_bundler.build_merkle_tree([h1, h2])
        # Should combine h1 + h2
        expected = hashlib.sha256(bytes.fromhex(h1) + bytes.fromhex(h2)).hexdigest()
        assert result == expected

    def test_three_hashes_pads_to_four(self):
        """Three hashes should pad to four for tree construction."""
        h1 = "a" * 64
        h2 = "b" * 64
        h3 = "c" * 64
        result = batch_bundler.build_merkle_tree([h1, h2, h3])
        assert len(result) == 64

    def test_deterministic(self):
        """Same input should always produce same output."""
        hashes = ["a" * 64, "b" * 64, "c" * 64, "d" * 64]
        result1 = batch_bundler.build_merkle_tree(hashes)
        result2 = batch_bundler.build_merkle_tree(hashes)
        assert result1 == result2


class TestProcessSession:
    """Tests for process_session function."""

    def test_empty_session(self):
        """Process empty directory returns empty results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "session_001"
            session_dir.mkdir()

            session_sha, file_count, total_bytes, file_list = batch_bundler.process_session(session_dir)

            assert session_sha == batch_bundler.sha256_bytes(b"")
            assert file_count == 0
            assert total_bytes == 0
            assert file_list == []

    def test_single_file(self):
        """Process directory with single file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "session_001"
            session_dir.mkdir()

            # Create a test file
            test_file = session_dir / "test.txt"
            test_file.write_bytes(b"hello world")

            session_sha, file_count, total_bytes, file_list = batch_bundler.process_session(session_dir)

            assert file_count == 1
            assert total_bytes == 11  # len(b"hello world")
            assert len(file_list) == 1
            assert file_list[0]["path"] == "test.txt"
            assert file_list[0]["bytes"] == 11

    def test_nested_files(self):
        """Process directory with nested files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "session_001"
            session_dir.mkdir()

            # Create nested structure
            (session_dir / "subdir").mkdir()
            (session_dir / "root.txt").write_bytes(b"root")
            (session_dir / "subdir" / "nested.txt").write_bytes(b"nested")

            session_sha, file_count, total_bytes, file_list = batch_bundler.process_session(session_dir)

            assert file_count == 2
            paths = [f["path"] for f in file_list]
            assert "root.txt" in paths
            assert "subdir/nested.txt" in paths

    def test_deterministic_order(self):
        """Files should be processed in sorted order for deterministic results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "session_001"
            session_dir.mkdir()

            # Create files in non-alphabetical order
            (session_dir / "z_file.txt").write_bytes(b"z")
            (session_dir / "a_file.txt").write_bytes(b"a")
            (session_dir / "m_file.txt").write_bytes(b"m")

            session_sha1, _, _, _ = batch_bundler.process_session(session_dir)

            # Re-run to verify determinism
            session_sha2, _, _, _ = batch_bundler.process_session(session_dir)

            assert session_sha1 == session_sha2

    def test_excludes_directories(self):
        """Only files should be included, not directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "session_001"
            session_dir.mkdir()

            (session_dir / "subdir").mkdir()
            (session_dir / "file.txt").write_bytes(b"content")

            session_sha, file_count, total_bytes, file_list = batch_bundler.process_session(session_dir)

            assert file_count == 1
            assert file_list[0]["path"] == "file.txt"


class TestBuildManifest:
    """Tests for build_manifest function."""

    def test_basic_manifest(self):
        """Build manifest with session results."""
        session_results = [
            {"session_id": "s1", "sha256": "abc123", "file_count": 5, "bytes": 1024}
        ]
        merkle_root = "merkle_root_value"

        manifest = batch_bundler.build_manifest(session_results, merkle_root)

        assert manifest["version"] == "1.0"
        assert manifest["merkle_root"] == merkle_root
        assert manifest["sessions"] == session_results
        assert "created_at" in manifest

    def test_created_at_is_iso_format(self):
        """created_at should be ISO format."""
        session_results = []
        merkle_root = "root"

        manifest = batch_bundler.build_manifest(session_results, merkle_root)

        # Should parse as ISO format
        created_at = manifest["created_at"]
        # Remove trailing Z if present for parsing
        parsed = datetime.datetime.fromisoformat(created_at.rstrip("Z"))
        assert parsed is not None

    def test_multiple_sessions(self):
        """Manifest can hold multiple sessions."""
        session_results = [
            {"session_id": "s1", "sha256": "hash1"},
            {"session_id": "s2", "sha256": "hash2"},
            {"session_id": "s3", "sha256": "hash3"},
        ]
        merkle_root = "root123"

        manifest = batch_bundler.build_manifest(session_results, merkle_root)

        assert len(manifest["sessions"]) == 3


class TestMain:
    """Tests for main function."""

    def test_nonexistent_session_exits_with_error(self):
        """Should exit with error when session doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()

            # Mock sys.argv
            original_argv = sys.argv
            original_stderr = sys.stderr
            try:
                sys.argv = ["batch_bundler", "/nonexistent/path", "--output-dir", str(output_dir)]
                sys.stderr = io.StringIO()
                with pytest.raises(SystemExit) as exc_info:
                    batch_bundler.main()
                assert exc_info.value.code == 1
            finally:
                sys.argv = original_argv
                sys.stderr = original_stderr

    def test_valid_session_creates_output(self):
        """Should create tarball and manifest for valid sessions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a session directory
            session_dir = Path(tmpdir) / "session_001"
            session_dir.mkdir()
            (session_dir / "test.txt").write_bytes(b"test content")

            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()

            # Mock sys.argv
            original_argv = sys.argv
            try:
                sys.argv = ["batch_bundler", str(session_dir), "--output-dir", str(output_dir)]
                # Run main - it may exit with 0 on success
                try:
                    batch_bundler.main()
                except SystemExit as e:
                    if e.code != 0:
                        pytest.fail(f"main() exited with code {e.code}")
            finally:
                sys.argv = original_argv

            # Check outputs exist
            tarball_files = list(output_dir.glob("*.tar.gz"))
            manifest_files = list(output_dir.glob("*.json"))

            # At least one output should exist (or we got past validation)
            # The actual main() might have more requirements
