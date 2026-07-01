"""Tests for bin/disk_space_manager.py — local clip storage LRU cleanup.

Covers the helper functions and DiskSpaceManager class:
- parse_size: human-readable size strings to bytes
- ClipMetadata: serialization round-trip
- DiskSpaceManager.get_current_usage / get_usage_percentage
- DiskSpaceManager.load_metadata / save_metadata
- DiskSpaceManager.get_clips_sorted_by_lru / get_deletable_clips
- DiskSpaceManager.check_and_warn (returns True iff above threshold)
- DiskSpaceManager.cleanup (dry-run, force, normal, missing-file skip, OSError)
- DiskSpaceManager.get_status_summary
- main() CLI entry point (--check, --status, --cleanup, --dry-run, errors)
"""

import json
import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from bin.disk_space_manager import (
    BYTE_TO_GB,
    DEFAULT_CAP_GB,
    ClipMetadata,
    DiskSpaceManager,
    main,
    parse_size,
)

# ------------------------- parse_size -------------------------


class TestParseSize:
    """Tests for parse_size helper."""

    def test_gigabytes(self):
        assert parse_size("5GB") == 5 * 1024**3

    def test_gigabytes_decimal(self):
        assert parse_size("1.5GB") == int(1.5 * 1024**3)

    def test_megabytes(self):
        assert parse_size("256MB") == 256 * 1024**2

    def test_kilobytes(self):
        assert parse_size("1024KB") == 1024 * 1024

    def test_bytes(self):
        assert parse_size("512B") == 512

    def test_terabytes(self):
        assert parse_size("2TB") == 2 * 1024**4

    def test_lowercase_unit(self):
        # Implementation upper-cases the suffix
        assert parse_size("5gb") == 5 * 1024**3

    def test_whitespace(self):
        assert parse_size("  10MB  ") == 10 * 1024**2

    def test_bare_number(self):
        assert parse_size("1234") == 1234

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid size"):
            parse_size("not-a-size")


# ------------------------- ClipMetadata -------------------------


class TestClipMetadata:
    """Tests for ClipMetadata serialization."""

    def _sample(self):
        return ClipMetadata(
            clip_id="clip-001",
            file_path=Path("/tmp/clips/clip-001.tar"),
            size_bytes=1024,
            last_accessed=datetime(2026, 1, 1, 12, 0, 0),
            status="uploaded",
        )

    def test_to_dict(self):
        clip = self._sample()
        d = clip.to_dict()
        assert d["clip_id"] == "clip-001"
        assert d["file_path"] == "/tmp/clips/clip-001.tar"
        assert d["size_bytes"] == 1024
        assert d["status"] == "uploaded"
        assert d["last_accessed"] == "2026-01-01T12:00:00"

    def test_from_dict_round_trip(self):
        clip = self._sample()
        d = clip.to_dict()
        restored = ClipMetadata.from_dict(d)
        assert restored.clip_id == clip.clip_id
        assert restored.file_path == clip.file_path
        assert restored.size_bytes == clip.size_bytes
        assert restored.status == clip.status
        assert restored.last_accessed == clip.last_accessed

    def test_from_dict_converts_path_string(self):
        clip = ClipMetadata.from_dict(
            {
                "clip_id": "x",
                "file_path": "/var/clips/x.tar",
                "size_bytes": 10,
                "last_accessed": "2026-05-01T00:00:00",
                "status": "pending",
            }
        )
        assert isinstance(clip.file_path, Path)
        assert str(clip.file_path) == "/var/clips/x.tar"


# ------------------------- DiskSpaceManager: init / usage -------------------------


class TestDiskSpaceManagerInit:
    """Tests for DiskSpaceManager construction and usage stats."""

    def test_missing_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            with pytest.raises(FileNotFoundError):
                DiskSpaceManager(missing)

    def test_default_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DiskSpaceManager(Path(tmp))
            assert mgr.cap_bytes == DEFAULT_CAP_GB * BYTE_TO_GB

    def test_custom_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DiskSpaceManager(Path(tmp), cap_bytes=10 * BYTE_TO_GB)
            assert mgr.cap_bytes == 10 * BYTE_TO_GB

    def test_default_metadata_path_inside_clips_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DiskSpaceManager(Path(tmp))
            assert mgr.metadata_path == Path(tmp) / ".clip_metadata.json"

    def test_custom_metadata_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "meta.json"
            mgr = DiskSpaceManager(Path(tmp), metadata_path=meta)
            assert mgr.metadata_path == meta

    def test_get_current_usage_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DiskSpaceManager(Path(tmp))
            assert mgr.get_current_usage() == 0

    def test_get_current_usage_sums_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "a.tar").write_bytes(b"x" * 100)
            (clips / "b.tar").write_bytes(b"y" * 200)
            mgr = DiskSpaceManager(clips)
            assert mgr.get_current_usage() == 300

    def test_get_current_usage_ignores_dotfiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "real.tar").write_bytes(b"a" * 10)
            (clips / ".hidden").write_bytes(b"b" * 999)
            mgr = DiskSpaceManager(clips)
            assert mgr.get_current_usage() == 10

    def test_get_usage_percentage(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "a.tar").write_bytes(b"x" * (BYTE_TO_GB // 2))
            mgr = DiskSpaceManager(clips, cap_bytes=BYTE_TO_GB)
            assert mgr.get_usage_percentage() == pytest.approx(0.5)


# ------------------------- metadata load/save -------------------------


class TestMetadataIO:
    """Tests for load_metadata / save_metadata."""

    def test_load_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DiskSpaceManager(Path(tmp))
            assert mgr.load_metadata() == {}

    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DiskSpaceManager(Path(tmp))
            clip = ClipMetadata(
                "c1",
                Path(tmp) / "c1.tar",
                42,
                datetime(2026, 1, 1),
                "uploaded",
            )
            mgr.save_metadata({"c1": clip})
            loaded = mgr.load_metadata()
            assert "c1" in loaded
            assert loaded["c1"].clip_id == "c1"
            assert loaded["c1"].size_bytes == 42

    def test_load_corrupt_returns_empty(self, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = DiskSpaceManager(Path(tmp))
            mgr.metadata_path.write_text("{not json", encoding="utf-8")
            with caplog.at_level(logging.WARNING):
                result = mgr.load_metadata()
            assert result == {}
            assert any("Failed to load metadata" in r.message for r in caplog.records)


# ------------------------- LRU and deletable -------------------------


class TestLruAndDeletable:
    """Tests for get_clips_sorted_by_lru and get_deletable_clips."""

    def test_sorted_by_last_accessed_ascending(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            old = clips / "old.tar"
            new = clips / "new.tar"
            old.write_bytes(b"o")
            new.write_bytes(b"n")
            now = datetime(2026, 1, 1)
            mgr = DiskSpaceManager(clips)
            mgr.save_metadata(
                {
                    "old": ClipMetadata(
                        "old", old, 1, now - timedelta(days=2), "uploaded"
                    ),
                    "new": ClipMetadata(
                        "new", new, 1, now, "uploaded"
                    ),
                }
            )
            ordered = mgr.get_clips_sorted_by_lru()
            assert [c.clip_id for c in ordered] == ["old", "new"]

    def test_untracked_file_uses_stat_for_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "ghost.tar").write_bytes(b"z" * 7)
            mgr = DiskSpaceManager(clips)
            ordered = mgr.get_clips_sorted_by_lru()
            assert len(ordered) == 1
            assert ordered[0].status == "local_only"
            assert ordered[0].size_bytes == 7

    def test_get_deletable_excludes_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "pending.tar").write_bytes(b"p")
            (clips / "uploaded.tar").write_bytes(b"u")
            now = datetime(2026, 1, 1)
            mgr = DiskSpaceManager(clips)
            mgr.save_metadata(
                {
                    "pending": ClipMetadata(
                        "pending", clips / "pending.tar", 1, now, "pending"
                    ),
                    "uploaded": ClipMetadata(
                        "uploaded", clips / "uploaded.tar", 1, now, "uploaded"
                    ),
                }
            )
            deletable = mgr.get_deletable_clips()
            ids = [c.clip_id for c in deletable]
            assert "pending" not in ids
            assert "uploaded" in ids


# ------------------------- check_and_warn -------------------------


class TestCheckAndWarn:
    """Tests for check_and_warn threshold logic."""

    def test_below_threshold(self, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            mgr = DiskSpaceManager(clips, cap_bytes=10 * BYTE_TO_GB)
            with caplog.at_level(logging.INFO):
                warned = mgr.check_and_warn()
            assert warned is False

    def test_at_threshold_warns(self, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            # Use 1 full GB on a 1 GB cap -> usage_pct = 1.0 >= 0.80
            (clips / "big.tar").write_bytes(b"x" * BYTE_TO_GB)
            mgr = DiskSpaceManager(clips, cap_bytes=BYTE_TO_GB)
            with caplog.at_level(logging.WARNING):
                warned = mgr.check_and_warn()
            assert warned is True
            assert any("Storage at" in r.message for r in caplog.records)


# ------------------------- cleanup -------------------------


class TestCleanup:
    """Tests for cleanup behaviour: under cap, over cap, dry-run, force, errors."""

    def test_under_cap_no_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "a.tar").write_bytes(b"a")
            mgr = DiskSpaceManager(clips, cap_bytes=10 * BYTE_TO_GB)
            deleted, freed = mgr.cleanup()
            assert deleted == 0
            assert freed == 0
            assert (clips / "a.tar").exists()

    def test_force_deletes_to_half_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            # 80MB of data, cap=100MB -> force should drop to 50MB target
            (clips / "a.tar").write_bytes(b"x" * int(0.8 * 100 * 1024 * 1024))
            mgr = DiskSpaceManager(
                clips, cap_bytes=int(100 * 1024 * 1024)
            )
            deleted, freed = mgr.cleanup(force=True)
            assert deleted >= 1
            assert freed >= int(0.3 * 100 * 1024 * 1024)

    def test_dry_run_does_not_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "a.tar").write_bytes(b"x" * (2 * 1024 * 1024))
            mgr = DiskSpaceManager(clips, cap_bytes=1024 * 1024)
            deleted, freed = mgr.cleanup(dry_run=True)
            assert deleted >= 1
            assert freed >= 1024 * 1024
            # File should still exist after dry-run
            assert (clips / "a.tar").exists()

    def test_missing_file_in_metadata_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            # metadata references a file that doesn't exist on disk
            now = datetime(2026, 1, 1)
            mgr = DiskSpaceManager(
                clips, cap_bytes=1024
            )
            mgr.save_metadata(
                {
                    "ghost": ClipMetadata(
                        "ghost",
                        Path(tmp) / "ghost.tar",
                        9999,
                        now,
                        "uploaded",
                    )
                }
            )
            # No real file on disk, so no deletion should occur
            deleted, freed = mgr.cleanup(force=True)
            assert deleted == 0
            assert freed == 0

    def test_oserror_logged_and_continues(self, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            f = clips / "a.tar"
            f.write_bytes(b"x" * (2 * 1024 * 1024))
            mgr = DiskSpaceManager(clips, cap_bytes=1024)
            with patch.object(Path, "unlink", side_effect=OSError("boom")):
                with caplog.at_level(logging.ERROR):
                    deleted, freed = mgr.cleanup()
            # All OSError-caught deletions are still counted (per implementation)
            assert deleted >= 0
            assert freed >= 0
            assert any("Failed to delete" in r.message for r in caplog.records)

    def test_skips_pending_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            pending_file = clips / "pending.tar"
            pending_file.write_bytes(b"x" * (2 * 1024 * 1024))
            mgr = DiskSpaceManager(
                clips, cap_bytes=1024
            )
            mgr.save_metadata(
                {
                    "pending": ClipMetadata(
                        "pending", pending_file, 2 * 1024 * 1024,
                        datetime(2026, 1, 1), "pending",
                    )
                }
            )
            deleted, _ = mgr.cleanup(force=True)
            assert deleted == 0
            assert pending_file.exists()


# ------------------------- get_status_summary -------------------------


class TestStatusSummary:
    """Tests for get_status_summary output shape."""

    def test_summary_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "a.tar").write_bytes(b"x" * 10)
            mgr = DiskSpaceManager(clips, cap_bytes=BYTE_TO_GB)
            summary = mgr.get_status_summary()
            for key in (
                "current_bytes",
                "cap_bytes",
                "usage_percentage",
                "total_clips",
                "status_counts",
                "above_warning",
            ):
                assert key in summary
            assert summary["total_clips"] == 1
            assert summary["status_counts"]["local_only"] == 1
            assert summary["above_warning"] is False
            assert summary["current_bytes"] == 10


# ------------------------- main() CLI -------------------------


class TestMain:
    """Tests for the CLI main() entry point."""

    def test_check_subcommand(self, capsys, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            with caplog.at_level(logging.INFO):
                rc = main(["--clips-dir", tmp, "--check"])
            assert rc == 0

    def test_status_subcommand_prints_json(self, capsys):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["--clips-dir", tmp, "--status"])
            captured = capsys.readouterr()
            assert rc == 0
            data = json.loads(captured.out)
            assert "usage_percentage" in data

    def test_cleanup_dry_run_subcommand(self, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "a.tar").write_bytes(b"x" * (2 * 1024 * 1024))
            # cap=10MB and file is 2MB -> well below warning threshold (0.8 * 10MB = 8MB)
            with caplog.at_level(logging.INFO):
                rc = main(
                    ["--clips-dir", str(clips), "--cap", "10MB", "--dry-run"]
                )
            assert rc == 0
            # File still present
            assert (clips / "a.tar").exists()

    def test_missing_dir_returns_nonzero(self, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "nope")
            with caplog.at_level(logging.ERROR):
                rc = main(["--clips-dir", missing, "--check"])
            assert rc == 1

    def test_invalid_cap_returns_nonzero(self, caplog):
        with tempfile.TemporaryDirectory() as tmp:
            with caplog.at_level(logging.ERROR):
                rc = main(["--clips-dir", tmp, "--cap", "bogus", "--check"])
            assert rc == 1

    def test_no_subcommand_returns_warning_code_when_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            (clips / "a.tar").write_bytes(b"x" * (2 * 1024 * 1024))
            rc = main(["--clips-dir", str(clips), "--cap", "1MB"])
            # Above warning -> exit 2
            assert rc == 2
