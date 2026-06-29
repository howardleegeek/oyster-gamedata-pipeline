#!/usr/bin/env python3
"""Tests for bin/edge_test_unicode_filenames.py — Boundary test for unicode filenames in tarballs.

Verifies tarball creation/extraction handles UTF-8 filenames: Chinese,
Japanese, Korean, emoji, RTL scripts, and accented characters.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = Path(__file__).parent.parent.parent / "bin" / "edge_test_unicode_filenames.py"


class TestEdgeTestUnicodeFilenames:
    """Test suite for edge_test_unicode_filenames.py."""

    def test_script_exists(self):
        """Verify the edge test script exists and is executable."""
        assert EDGE_TEST_SCRIPT.exists(), f"Script not found: {EDGE_TEST_SCRIPT}"

    def test_runs_successfully(self):
        """Verify the edge test runs and exits with success (0)."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

    def test_verbose_mode(self):
        """Verify verbose mode works without errors."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Verbose mode failed: {result.stderr}"
        # Verify expected output markers are present
        assert "PASS" in result.stdout

    def test_unicode_names_list_not_empty(self):
        """Verify the UNICODE_NAMES list is populated with diverse entries."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_unicode_filenames import UNICODE_NAMES

        assert len(UNICODE_NAMES) >= 5, "Should have at least 5 unicode filenames"
        # Ensure entries are not ASCII-only (validates intent of test)
        for name in UNICODE_NAMES:
            assert any(ord(c) > 127 for c in name), f"Name not unicode: {name!r}"

    def test_create_unicode_tarball(self):
        """Verify _create_unicode_tarball creates a valid tarball with all entries."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_unicode_filenames import (
            UNICODE_NAMES,
            _create_unicode_tarball,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, "test.tar.gz")
            entries = _create_unicode_tarball(tar_path)
            assert os.path.isfile(tar_path)
            assert len(entries) == len(UNICODE_NAMES)
            # Verify the tarball can be opened and contains expected names
            with tarfile.open(tar_path, "r:gz") as tf:
                members = tf.getnames()
                assert len(members) == len(UNICODE_NAMES)
                for name, _ in entries:
                    assert name in members

    def test_verify_tarball_passes_for_valid_archive(self):
        """Verify _verify_tarball returns True for a freshly created tarball."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_unicode_filenames import (
            _create_unicode_tarball,
            _verify_tarball,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, "test.tar.gz")
            entries = _create_unicode_tarball(tar_path)
            assert _verify_tarball(tar_path, entries) is True

    def test_verify_tarball_detects_missing_entry(self):
        """Verify _verify_tarball returns False when an entry is missing."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_unicode_filenames import (
            _create_unicode_tarball,
            _verify_tarball,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, "test.tar.gz")
            real_entries = _create_unicode_tarball(tar_path)
            # Forge a fake "expected" list with a non-existent entry appended
            fake_entries = list(real_entries) + [("not_in_archive.txt", b"x")]
            assert _verify_tarball(tar_path, fake_entries) is False

    def test_verify_extraction_round_trip(self):
        """Verify _verify_extraction returns True for a valid tarball."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_unicode_filenames import (
            _create_unicode_tarball,
            _verify_extraction,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, "test.tar.gz")
            entries = _create_unicode_tarball(tar_path)
            assert _verify_extraction(tar_path, entries) is True

    def test_existing_tarball_mode(self):
        """Verify --tarball mode inspects an existing archive without failure."""
        # Create a tarball on disk and feed it to the script via --tarball
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_unicode_filenames import (
            UNICODE_NAMES,
            _create_unicode_tarball,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, "existing.tar.gz")
            _create_unicode_tarball(tar_path)
            result = subprocess.run(
                [sys.executable, str(EDGE_TEST_SCRIPT), "--tarball", tar_path, "--verbose"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            assert "Verifying existing tarball" in result.stdout
            assert f"Total entries: {len(UNICODE_NAMES)}" in result.stdout

    def test_round_trip_preserves_unicode_content(self):
        """Verify content round-trips through tarball with unicode names intact."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_unicode_filenames import UNICODE_NAMES

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, "rt.tar.gz")
            # Write each name as a tar member with a payload that contains
            # the unicode name itself (validates both name and content).
            with tarfile.open(tar_path, "w:gz") as tf:
                for name in UNICODE_NAMES:
                    payload = f"content-for-{name}".encode("utf-8")
                    info = tarfile.TarInfo(name=name)
                    info.size = len(payload)
                    tf.addfile(info, io.BytesIO(payload))

            with tarfile.open(tar_path, "r:gz") as tf:
                members = {tf.extractfile(m).read(): m.name for m in tf.getmembers() if tf.extractfile(m) is not None}
            for name in UNICODE_NAMES:
                expected = f"content-for-{name}".encode("utf-8")
                assert expected in members, f"Missing payload for {name!r}"
                assert members[expected] == name, f"Name mismatch for {name!r}"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
