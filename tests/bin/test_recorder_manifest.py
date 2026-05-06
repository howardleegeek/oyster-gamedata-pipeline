#!/usr/bin/env python3
"""Tests for bin/recorder_manifest.py (spec G262)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from bin.recorder_manifest import (  # noqa: E402
    CANONICAL_DIR,
    CANONICAL_FILES,
    MANIFEST_NAME,
    build_manifest,
    hash_directory,
    hash_file,
    main,
    write_manifest,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_canonical_set_matches_prd() -> None:
    """Five entries: 4 files + 1 directory."""
    assert CANONICAL_FILES == [
        "video.mp4",
        "systeminfo.json",
        "action_camera.json",
        "gameinfo.xlsx",
    ]
    assert CANONICAL_DIR == "depth"


def test_hash_file_matches_sha256(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello-oyster")
    assert hash_file(f) == _sha256(b"hello-oyster")


def test_hash_directory_rollup(tmp_path: Path) -> None:
    d = tmp_path / "depth"
    d.mkdir()
    (d / "frame_000001.exr").write_bytes(b"a")
    (d / "frame_000002.exr").write_bytes(b"bb")
    meta = hash_directory(d)
    assert meta["file_count"] == 2
    assert meta["total_bytes"] == 3
    assert len(meta["sha256_of_sha256s"]) == 64
    assert set(meta["children"].keys()) == {"frame_000001.exr", "frame_000002.exr"}


def test_build_manifest_complete(tmp_path: Path) -> None:
    """All five canonical entries present → complete=True."""
    for name in CANONICAL_FILES:
        (tmp_path / name).write_bytes(b"x")
    depth = tmp_path / CANONICAL_DIR
    depth.mkdir()
    (depth / "frame_000001.exr").write_bytes(b"y")

    manifest = build_manifest(tmp_path)
    assert manifest["schema_version"] == "1"
    assert manifest["clip_id"] == tmp_path.name
    assert manifest["complete"] is True
    for name in CANONICAL_FILES:
        assert isinstance(manifest["files"][name], str)
    assert manifest["depth"]["file_count"] == 1


def test_build_manifest_partial(tmp_path: Path) -> None:
    """Missing files → complete=False, missing entries are None."""
    (tmp_path / "video.mp4").write_bytes(b"z")
    manifest = build_manifest(tmp_path)
    assert manifest["complete"] is False
    assert manifest["files"]["video.mp4"] is not None
    assert manifest["files"]["gameinfo.xlsx"] is None
    assert manifest["depth"] is None


def test_write_manifest_persists_json(tmp_path: Path) -> None:
    (tmp_path / "video.mp4").write_bytes(b"x")
    out = write_manifest(tmp_path)
    assert out == tmp_path / MANIFEST_NAME
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["clip_id"] == tmp_path.name


def test_write_manifest_invalid_dir() -> None:
    with pytest.raises(NotADirectoryError):
        write_manifest(Path("/nonexistent_xyz_for_test"))


def test_main_invalid_clip_returns_2(tmp_path: Path) -> None:
    rc = main(["--clip-dir", str(tmp_path / "missing")])
    assert rc == 2


def test_main_happy_path(tmp_path: Path) -> None:
    (tmp_path / "video.mp4").write_bytes(b"hi")
    rc = main(["--clip-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / MANIFEST_NAME).is_file()
