#!/usr/bin/env python3
"""
test_batch_bundler.py — Tests for batch_bundler.py
"""

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

# Ensure bin/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

from batch_bundler import (
    build_manifest,
    build_merkle_tree,
    process_session,
    sha256_file,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── test_sha256_file ──────────────────────────────────────────────
def test_sha256_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world")
    expected = _sha256(b"hello world")
    assert sha256_file(f) == expected


# ── test_build_merkle_tree ────────────────────────────────────────
def test_build_merkle_tree():
    h1 = _sha256(b"alpha")
    h2 = _sha256(b"beta")
    # Two leaves → one parent
    root = build_merkle_tree([h1, h2])
    expected = _sha256(bytes.fromhex(h1) + bytes.fromhex(h2))
    assert root == expected

    # Single leaf → padded with zero_hash
    root_single = build_merkle_tree([h1])
    zero = _sha256(b"")
    expected_single = _sha256(bytes.fromhex(h1) + bytes.fromhex(zero))
    assert root_single == expected_single

    # Empty list
    assert build_merkle_tree([]) == _sha256(b"")


# ── test_process_session ──────────────────────────────────────────
def test_process_session(tmp_path):
    session = tmp_path / "session_a"
    session.mkdir()
    files = {
        "file1.txt": b"content one",
        "file2.txt": b"content two",
    }
    for name, content in files.items():
        (session / name).write_bytes(content)

    session_sha256, file_count, total_bytes, file_list = process_session(session)

    assert file_count == 2
    assert total_bytes == len(b"content one") + len(b"content two")

    # Verify session_sha256 matches sorted-hash concatenation
    expected_session_hash = _sha256(
        "".join(sorted([_sha256(content) for content in files.values()])).encode()
    )
    assert session_sha256 == expected_session_hash


# ── test_build_manifest ───────────────────────────────────────────
def test_build_manifest():
    session_results = [
        {
            "session_id": "s1",
            "session_sha256": "abc123",
            "file_count": 2,
            "total_bytes": 100,
            "files": [],
        }
    ]
    merkle_root = "deadbeef"
    manifest = build_manifest(session_results, merkle_root)

    assert manifest["version"] == "1.0"
    assert manifest["merkle_root"] == merkle_root
    assert len(manifest["sessions"]) == 1
    assert manifest["sessions"][0]["session_id"] == "s1"
    assert "created_at" in manifest


# ── test_integration ──────────────────────────────────────────────
def test_integration(tmp_path):
    # Create session_a with a subdir
    session_a = tmp_path / "session_a"
    session_a.mkdir()
    (session_a / "file1.txt").write_text("hello")
    (session_a / "file2.txt").write_text("world")
    subdir = session_a / "subdir"
    subdir.mkdir()
    (subdir / "file3.txt").write_text("nested")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent.parent / "bin" / "batch_bundler.py"),
            str(session_a),
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    # Check tarball exists
    tarball = output_dir / "bundle.tar.gz"
    assert tarball.exists()

    # Check tarball arcname: subdir/file3.txt should be session_a/subdir/file3.txt
    with tarfile.open(tarball, "r:gz") as tar:
        names = tar.getnames()
        assert (
            "session_a/subdir/file3.txt" in names
        ), f"Expected session_a/subdir/file3.txt in {names}"
        assert "session_a/file1.txt" in names
        assert "session_a/file2.txt" in names

    # Check manifest
    manifest_path = output_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["merkle_root"] is not None
    assert len(manifest["sessions"]) == 1


# ── test_cli_error_cases ──────────────────────────────────────────
def test_cli_error_cases(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent.parent / "bin" / "batch_bundler.py"),
            "/nonexistent/session_path",
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr or "Error" in result.stderr


# ── test_cli_help ─────────────────────────────────────────────────
def test_cli_help():
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent.parent / "bin" / "batch_bundler.py"),
            "--help",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Batch bundler" in result.stdout or "batch_bundler" in result.stdout
