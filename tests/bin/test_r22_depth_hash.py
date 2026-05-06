"""Unit tests for R22 — depth-content SHA-256 manifest binding.

Spec: docs/RED_TEAM_TAXONOMY.md § D-04. Covers the happy path, two
attack residuals (content-modified, file-missing), the manifest-as-
authoritative semantics (extra unlisted file ⇒ PASS), and the two
IL10 ABSTAIN gates (missing depth_dir, missing manifest_path).
"""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from bin.v1_claude_residuals import ResidualResult, r22_depth_hash


def _write_exr_files(dir_path: Path, count: int) -> dict[str, str]:
    """Create deterministic ``depth_<n>.exr`` files; return manifest dict."""
    manifest: dict[str, str] = {}
    for i in range(count):
        name = f"depth_{i:05d}.exr"
        # Distinct content per file so a swap detection is meaningful.
        body = f"frame-{i}-payload-{'x' * (i % 7)}".encode("utf-8")
        (dir_path / name).write_bytes(body)
        manifest[name] = hashlib.sha256(body).hexdigest()
    return manifest


def _write_manifest(path: Path, manifest: dict[str, str]) -> None:
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


class TestR22DepthHash(unittest.TestCase):
    """Six fixtures: happy + 2 attacks + extra-file + 2 ABSTAIN gates."""

    def test_happy_path_passes(self) -> None:
        """5 EXR files all hash-match the manifest → PASS, residual 0."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = _write_exr_files(tmp_path, 5)
            mpath = tmp_path.parent / "depth_manifest.json"
            # write manifest beside (not inside) depth_dir to mirror packaging
            mpath = tmp_path / "_manifest.json"
            _write_manifest(mpath, manifest)
            result = r22_depth_hash({}, depth_dir=tmp_path, manifest_path=mpath)
        self.assertIsInstance(result, ResidualResult)
        self.assertEqual(result.name, "R22")
        self.assertTrue(result.passed)
        self.assertEqual(result.residual, 0.0)

    def test_content_modified_fails(self) -> None:
        """Tamper byte 0 of one file → residual=1, FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = _write_exr_files(tmp_path, 5)
            mpath = tmp_path / "_manifest.json"
            _write_manifest(mpath, manifest)
            # Adversary action: rewrite depth_00002.exr with different bytes.
            (tmp_path / "depth_00002.exr").write_bytes(b"TAMPERED-PAYLOAD")
            result = r22_depth_hash({}, depth_dir=tmp_path, manifest_path=mpath)
        self.assertFalse(result.passed)
        self.assertEqual(result.residual, 1.0)
        self.assertIn("mismatched=1", result.note)

    def test_missing_file_fails(self) -> None:
        """Delete one EXR → residual=1, FAIL with missing=1 in note."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = _write_exr_files(tmp_path, 5)
            mpath = tmp_path / "_manifest.json"
            _write_manifest(mpath, manifest)
            (tmp_path / "depth_00003.exr").unlink()
            result = r22_depth_hash({}, depth_dir=tmp_path, manifest_path=mpath)
        self.assertFalse(result.passed)
        self.assertEqual(result.residual, 1.0)
        self.assertIn("missing=1", result.note)

    def test_extra_unlisted_file_passes(self) -> None:
        """Manifest is authoritative — adversary-injected unlisted file
        is tolerated since the recorder didn't sign it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = _write_exr_files(tmp_path, 5)
            mpath = tmp_path / "_manifest.json"
            _write_manifest(mpath, manifest)
            (tmp_path / "depth_99999.exr").write_bytes(b"injected by attacker")
            result = r22_depth_hash({}, depth_dir=tmp_path, manifest_path=mpath)
        self.assertTrue(result.passed)
        self.assertEqual(result.residual, 0.0)

    def test_missing_depth_dir_abstains(self) -> None:
        """depth_dir=None → ABSTAIN (residual NaN, note prefix)."""
        with tempfile.TemporaryDirectory() as tmp:
            mpath = Path(tmp) / "_manifest.json"
            _write_manifest(mpath, {"foo.exr": "0" * 64})
            result = r22_depth_hash({}, depth_dir=None, manifest_path=mpath)
        self.assertFalse(result.passed)
        self.assertTrue(math.isnan(result.residual))
        self.assertTrue(result.note.startswith("ABSTAIN:"))
        self.assertIn("no_depth_dir", result.note)

    def test_missing_manifest_path_abstains(self) -> None:
        """manifest_path=None → ABSTAIN (legacy clip predating R22)."""
        with tempfile.TemporaryDirectory() as tmp:
            result = r22_depth_hash({}, depth_dir=tmp, manifest_path=None)
        self.assertFalse(result.passed)
        self.assertTrue(math.isnan(result.residual))
        self.assertTrue(result.note.startswith("ABSTAIN:"))
        self.assertIn("no_manifest_path", result.note)


if __name__ == "__main__":
    unittest.main()
