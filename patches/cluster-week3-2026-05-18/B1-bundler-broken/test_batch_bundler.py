#!/usr/bin/env python3
"""
Tests for batch_bundler.py
"""

import os
import sys
import json
import hashlib
import tarfile
import tempfile
from pathlib import Path
import pytest

# Add bin to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'bin'))

from batch_bundler import sha256_file, sha256_bytes, build_merkle_tree, process_session


def test_sha256_file():
    """Test SHA256 file hash calculation."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("test content")
        temp_path = f.name
    
    try:
        # Calculate hash manually
        expected = hashlib.sha256(b"test content").hexdigest()
        actual = sha256_file(Path(temp_path))
        assert actual == expected
    finally:
        os.unlink(temp_path)


def test_sha256_bytes():
    """Test SHA256 bytes hash calculation."""
    data = b"test content"
    expected = hashlib.sha256(data).hexdigest()
    actual = sha256_bytes(data)
    assert actual == expected


def test_build_merkle_tree():
    """Test Merkle tree construction."""
    # Test with empty list
    root = build_merkle_tree([])
    expected_empty = hashlib.sha256(b'').hexdigest()
    assert root == expected_empty
    
    # Test with single hash
    hash1 = hashlib.sha256(b"file1").hexdigest()
    root = build_merkle_tree([hash1])
    
    # For single hash, it should be hashed with zero hash
    zero_hash = hashlib.sha256(b'').hexdigest()
    zero_hash_bytes = bytes.fromhex(zero_hash)
    hash1_bytes = bytes.fromhex(hash1)
    expected = hashlib.sha256(hash1_bytes + zero_hash_bytes).hexdigest()
    assert root == expected
    
    # Test with two hashes
    hash2 = hashlib.sha256(b"file2").hexdigest()
    root = build_merkle_tree([hash1, hash2])
    
    hash2_bytes = bytes.fromhex(hash2)
    expected = hashlib.sha256(hash1_bytes + hash2_bytes).hexdigest()
    assert root == expected
    
    # Test with three hashes (should pad to 4)
    hash3 = hashlib.sha256(b"file3").hexdigest()
    root = build_merkle_tree([hash1, hash2, hash3])
    
    # Should be: root = hash(hash(h1+h2) + hash(h3+zero))
    hash3_bytes = bytes.fromhex(hash3)
    left_child = hashlib.sha256(hash1_bytes + hash2_bytes).digest()
    right_child = hashlib.sha256(hash3_bytes + zero_hash_bytes).digest()
    expected = hashlib.sha256(left_child + right_child).hexdigest()
    assert root == expected


def test_process_session():
    """Test processing a session directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir) / "test_session"
        session_dir.mkdir()
        
        # Create test files
        files = {
            "file1.txt": b"content1",
            "subdir/file2.txt": b"content2",
            "file3.txt": b"content3",
        }
        
        for rel_path, content in files.items():
            file_path = session_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)
        
        # Process session
        session_id, file_list, file_count, total_bytes, session_sha256 = process_session(session_dir)
        
        assert session_id == "test_session"
        assert file_count == 3
        assert total_bytes == len(b"content1") + len(b"content2") + len(b"content3")
        
        # Check file list
        assert len(file_list) == 3
        
        # Files should be sorted by path
        paths = [f["path"] for f in file_list]
        assert paths == ["file1.txt", "file3.txt", "subdir/file2.txt"]
        
        # Check hashes
        for file_info in file_list:
            expected_hash = hashlib.sha256(files[file_info["path"]]).hexdigest()
            assert file_info["sha256"] == expected_hash
        
        # Check session SHA256
        concat_hashes = ''.join(sorted(
            hashlib.sha256(content).hexdigest()
            for content in files.values()
        ))
        expected_session_hash = hashlib.sha256(concat_hashes.encode('utf-8')).hexdigest()
        assert session_sha256 == expected_session_hash


def test_integration():
    """Integration test with 2 fake sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test session directories
        sessions = {}
        
        # Session A
        session_a = Path(tmpdir) / "session_a"
        session_a.mkdir()
        (session_a / "file1.txt").write_text("test-a-1")
        (session_a / "file2.txt").write_text("test-a-2")
        (session_a / "subdir" / "file3.txt").write_text("test-a-3")
        sessions["session_a"] = session_a
        
        # Session B
        session_b = Path(tmpdir) / "session_b"
        session_b.mkdir()
        (session_b / "file1.txt").write_text("test-b-1")
        (session_b / "file2.txt").write_text("test-b-2")
        sessions["session_b"] = session_b
        
        # Create output directory
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        
        # Run batch bundler
        import subprocess
        result = subprocess.run([
            sys.executable, "bin/batch_bundler.py",
            str(session_a), str(session_b),
            "--output-dir", str(output_dir)
        ], capture_output=True, text=True)
        
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        
        assert result.returncode == 0
        
        # Check output files
        output_files = list(output_dir.iterdir())
        assert len(output_files) == 2
        
        # Find tarball and manifest
        tarball = None
        manifest = None
        
        for f in output_files:
            if f.name.endswith(".tar.gz"):
                tarball = f
            elif f.name.endswith(".manifest.json"):
                manifest = f
        
        assert tarball is not None
        assert manifest is not None
        
        # Load manifest
        with open(manifest, 'r') as f:
            manifest_data = json.load(f)
        
        # Check manifest structure
        assert "batch_id" in manifest_data
        assert manifest_data["batch_id"].startswith("oyster-batch-")
        assert "created_at_utc" in manifest_data
        assert manifest_data["session_count"] == 2
        assert manifest_data["total_files"] == 5  # 3 from session_a + 2 from session_b
        assert "merkle_root" in manifest_data
        assert "tarball_filename" in manifest_data
        assert "tarball_sha256" in manifest_data
        assert "sessions" in manifest_data
        assert len(manifest_data["sessions"]) == 2
        
        # Check session data
        session_data = {s["session_id"]: s for s in manifest_data["sessions"]}
        assert "session_a" in session_data
        assert "session_b" in session_data
        
        assert session_data["session_a"]["file_count"] == 3
        assert session_data["session_b"]["file_count"] == 2
        
        # Verify tarball SHA256 matches actual
        tarball_hash = hashlib.sha256()
        with open(tarball, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                tarball_hash.update(chunk)
        
        assert manifest_data["tarball_sha256"] == tarball_hash.hexdigest()
        
        # Extract tarball and verify contents
        with tarfile.open(tarball, 'r:gz') as tar:
            # Get all members
            members = tar.getmembers()
            
            # Check structure
            member_names = [m.name for m in members]
            assert "session_a/file1.txt" in member_names
            assert "session_a/file2.txt" in member_names
            assert "session_a/subdir/file3.txt" in member_names
            assert "session_b/file1.txt" in member_names
            assert "session_b/file2.txt" in member_names
            
            # Verify file contents
            for session_name, session_dir in sessions.items():
                for file_path in session_dir.rglob("*"):
                    if file_path.is_file():
                        rel_path = file_path.relative_to(session_dir)
                        tar_path = f"{session_name}/{rel_path}"
                        
                        # Extract and compare
                        member = tar.getmember(tar_path)
                        extracted_file = tar.extractfile(member)
                        original_content = file_path.read_bytes()
                        extracted_content = extracted_file.read()
                        
                        assert original_content == extracted_content
        
        # Verify Merkle root is deterministic
        # Run again with same inputs should produce same Merkle root
        output_dir2 = Path(tmpdir) / "output2"
        output_dir2.mkdir()
        
        result2 = subprocess.run([
            sys.executable, "bin/batch_bundler.py",
            str(session_a), str(session_b),
            "--output-dir", str(output_dir2)
        ], capture_output=True, text=True)
        
        assert result2.returncode == 0
        
        # Find manifest from second run
        manifest2 = None
        for f in output_dir2.iterdir():
            if f.name.endswith(".manifest.json"):
                manifest2 = f
                break
        
        assert manifest2 is not None
        
        with open(manifest2, 'r') as f:
            manifest_data2 = json.load(f)
        
        # Merkle root should be the same
        assert manifest_data["merkle_root"] == manifest_data2["merkle_root"]
        
        # Verify file SHA256 in manifest matches actual file SHA256
        for session_info in manifest_data["sessions"]:
            session_id = session_info["session_id"]
            session_dir = sessions[session_id]
            
            for file_info in session_info["files"]:
                file_path = session_dir / file_info["path"]
                actual_hash = sha256_file(file_path)
                assert file_info["sha256"] == actual_hash


def test_cli_help():
    """Test CLI help output."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "bin/batch_bundler.py", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert "session directories" in result.stdout.lower()


def test_cli_error_cases():
    """Test CLI error cases."""
    import subprocess
    
    # Test with non-existent directory
    result = subprocess.run(
        [sys.executable, "bin/batch_bundler.py", "/non/existent/dir", "--output-dir", "/tmp"],
        capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr or "Error" in result.stderr
    
    # Test with file instead of directory
    with tempfile.NamedTemporaryFile() as f:
        result = subprocess.run(
            [sys.executable, "bin/batch_bundler.py", f.name, "--output-dir", "/tmp"],
            capture_output=True, text=True
        )
        assert result.returncode != 0
        assert "Not a directory" in result.stderr or "Error" in result.stderr
    
    # Test missing required argument
    result = subprocess.run(
        [sys.executable, "bin/batch_bundler.py", "--output-dir", "/tmp"],
        capture_output=True, text=True
    )
    assert result.returncode != 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
