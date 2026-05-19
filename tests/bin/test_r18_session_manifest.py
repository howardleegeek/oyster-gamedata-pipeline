"""Unit tests for R18 — session_id manifest binding.

Spec: docs/RED_TEAM_TAXONOMY.md § R18. Closes Frankenstein-splice attack
B-05 by requiring every action_camera frame to declare a session_id
matching the recorder's session_manifest.json.

Six fixtures cover:
1. PASS — manifest + frame agree.
2. FAIL — manifest + frame disagree (splice).
3. ABSTAIN — no manifest path supplied (residual=NaN).
4. ABSTAIN — manifest exists but missing session_id key.
5. ABSTAIN — frame has no session_id field (legacy data).
6. ABSTAIN — manifest session_id is the empty string.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from bin.v1_claude_residuals import ResidualResult, r18_session_manifest


def _write_manifest(dir_path: Path, payload: dict) -> Path:
    """Write a session_manifest.json fixture and return its path."""
    mpath = dir_path / "session_manifest.json"
    mpath.write_text(json.dumps(payload), encoding="utf-8")
    return mpath


class TestR18SessionManifest(unittest.TestCase):
    """Six fixtures cover PASS / FAIL splice / four ABSTAIN gates."""

    def test_matching_session_id_passes(self) -> None:
        """Manifest and frame share the same UUID → PASS, residual 0."""
        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            mpath = _write_manifest(Path(tmp), {"session_id": sid})
            frame = {"frame": 0, "session_id": sid}
            result = r18_session_manifest(frame, manifest_path=mpath)
        self.assertIsInstance(result, ResidualResult)
        self.assertEqual(result.name, "R18")
        self.assertTrue(result.passed)
        self.assertEqual(result.residual, 0.0)
        self.assertFalse(result.note.startswith("ABSTAIN:"))

    def test_mismatched_session_id_fails(self) -> None:
        """Splice attack: frame from clip A, manifest from clip B → FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            mpath = _write_manifest(Path(tmp), {"session_id": "manifest-uuid"})
            frame = {"frame": 0, "session_id": "frame-from-other-clip"}
            result = r18_session_manifest(frame, manifest_path=mpath)
        self.assertFalse(result.passed)
        self.assertEqual(result.residual, 1.0)
        self.assertFalse(result.note.startswith("ABSTAIN:"))
        self.assertIn("mismatch", result.note)

    def test_missing_manifest_path_abstains(self) -> None:
        """manifest_path=None → ABSTAIN (residual NaN, can't vote)."""
        result = r18_session_manifest({"session_id": "x"}, manifest_path=None)
        self.assertFalse(result.passed)
        self.assertTrue(math.isnan(result.residual))
        self.assertTrue(result.note.startswith("ABSTAIN:"))
        self.assertIn("no_manifest_path", result.note)

    def test_manifest_without_session_id_abstains(self) -> None:
        """Manifest exists but lacks session_id key → ABSTAIN."""
        with tempfile.TemporaryDirectory() as tmp:
            mpath = _write_manifest(Path(tmp), {"recorder_version": "lite-v0.21.0"})
            result = r18_session_manifest({"session_id": "x"}, manifest_path=mpath)
        self.assertFalse(result.passed)
        self.assertTrue(math.isnan(result.residual))
        self.assertTrue(result.note.startswith("ABSTAIN:"))
        self.assertIn("manifest_no_session_id", result.note)

    def test_frame_without_session_id_abstains(self) -> None:
        """Legacy frame predating R18 (no session_id key) → ABSTAIN."""
        with tempfile.TemporaryDirectory() as tmp:
            mpath = _write_manifest(Path(tmp), {"session_id": "valid-uuid"})
            legacy_frame = {"frame": 0, "fps": 30.0}  # no session_id
            result = r18_session_manifest(legacy_frame, manifest_path=mpath)
        self.assertFalse(result.passed)
        self.assertTrue(math.isnan(result.residual))
        self.assertTrue(result.note.startswith("ABSTAIN:"))
        self.assertIn("frame_no_session_id", result.note)

    def test_empty_session_id_abstains(self) -> None:
        """Empty-string session_id (uninitialized recorder) → ABSTAIN, not FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            mpath = _write_manifest(Path(tmp), {"session_id": ""})
            frame = {"frame": 0, "session_id": "anything"}
            result = r18_session_manifest(frame, manifest_path=mpath)
        self.assertFalse(result.passed)
        self.assertTrue(math.isnan(result.residual))
        self.assertTrue(result.note.startswith("ABSTAIN:"))
        self.assertIn("manifest_session_id_empty", result.note)


if __name__ == "__main__":
    unittest.main()
