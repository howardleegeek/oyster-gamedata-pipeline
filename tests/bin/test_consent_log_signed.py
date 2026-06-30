#!/usr/bin/env python3
"""Tests for bin/consent_log_signed.py — G221 signed consent log.

Covers:
  * ConsentEntry class (init, to_dict, from_dict)
  * ConsentLogSigned class (key validation, key loading/saving, log file creation)
  * HMAC-SHA256 signature computation and verification
  * add_entry (creates signed entries)
  * verify_entry (validates signature)
  * verify_all (bulk verification)
  * CLI: add, verify, list commands
  * Error handling: invalid key length, corrupted log file, malformed JSON
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from consent_log_signed import (  # noqa: E402
    ConsentEntry,
    ConsentLogError,
    ConsentLogSigned,
    main,
)

# ---------------------------------------------------------------------------
# ConsentEntry
# ---------------------------------------------------------------------------


class TestConsentEntry:
    """ConsentEntry: serialization and deserialization."""

    def test_init_with_defaults(self):
        """Default timestamp and opt_in_version when not provided."""
        entry = ConsentEntry(user_id="user123", game="minecraft")
        assert entry.user_id == "user123"
        assert entry.game == "minecraft"
        assert entry.opt_in_version == "1.0"
        assert entry.timestamp is not None
        assert entry.signature is None

    def test_init_with_custom_values(self):
        """Custom values are stored correctly."""
        ts = "2026-06-30T10:00:00+00:00"
        entry = ConsentEntry(
            user_id="user456",
            game="factorio",
            timestamp=ts,
            opt_in_version="2.0",
            signature="somesig",
        )
        assert entry.user_id == "user456"
        assert entry.game == "factorio"
        assert entry.timestamp == ts
        assert entry.opt_in_version == "2.0"
        assert entry.signature == "somesig"

    def test_to_dict(self):
        """to_dict returns expected dictionary."""
        entry = ConsentEntry(
            user_id="user789",
            game="minecraft",
            timestamp="2026-06-30T10:00:00+00:00",
            opt_in_version="1.5",
            signature="abc123",
        )
        d = entry.to_dict()
        assert d == {
            "user_id": "user789",
            "game": "minecraft",
            "timestamp": "2026-06-30T10:00:00+00:00",
            "opt_in_version": "1.5",
            "signature": "abc123",
        }

    def test_from_dict(self):
        """from_dict reconstructs ConsentEntry."""
        data = {
            "user_id": "user999",
            "game": "terraria",
            "timestamp": "2026-06-30T11:00:00+00:00",
            "opt_in_version": "3.0",
            "signature": "xyz789",
        }
        entry = ConsentEntry.from_dict(data)
        assert entry.user_id == "user999"
        assert entry.game == "terraria"
        assert entry.timestamp == "2026-06-30T11:00:00+00:00"
        assert entry.opt_in_version == "3.0"
        assert entry.signature == "xyz789"

    def test_roundtrip_to_from_dict(self):
        """to_dict -> from_dict preserves all fields."""
        original = ConsentEntry(
            user_id="user_round",
            game="valheim",
            timestamp="2026-06-30T12:00:00+00:00",
            opt_in_version="1.2",
            signature="sig456",
        )
        restored = ConsentEntry.from_dict(original.to_dict())
        assert restored.user_id == original.user_id
        assert restored.game == original.game
        assert restored.timestamp == original.timestamp
        assert restored.opt_in_version == original.opt_in_version
        assert restored.signature == original.signature


# ---------------------------------------------------------------------------
# ConsentLogSigned - Key Management
# ---------------------------------------------------------------------------


class TestConsentLogSignedKeyManagement:
    """Key validation, loading, and saving."""

    def test_init_with_explicit_key(self):
        """Explicit secret_key is stored and validated."""
        key = b"a" * 32  # 32 bytes >= MIN_KEY_LENGTH
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            assert log.secret_key == key

    def test_init_key_too_short_raises(self):
        """Key shorter than MIN_KEY_LENGTH raises ConsentLogError."""
        key = b"short"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            with pytest.raises(ConsentLogError, match="at least 16 bytes"):
                ConsentLogSigned(log_file=log_file, secret_key=key)

    def test_init_creates_key_file_when_specified(self):
        """When key_file specified but not exists, generates and saves key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            key_file = Path(tmpdir) / "key.txt"
            log = ConsentLogSigned(log_file=log_file, key_file=key_file)
            assert key_file.exists()
            # Key file should be valid base64
            saved_key = base64.b64decode(key_file.read_text().strip())
            assert saved_key == log.secret_key

    def test_init_loads_existing_key_file(self):
        """When key_file exists, loads the key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            key = b"b" * 32
            key_file = Path(tmpdir) / "key.txt"
            key_file.write_text(base64.b64encode(key).decode())
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, key_file=key_file)
            assert log.secret_key == key


# ---------------------------------------------------------------------------
# ConsentLogSigned - Log File Management
# ---------------------------------------------------------------------------


class TestConsentLogSignedLogFile:
    """Log file creation and format."""

    def test_init_creates_log_file(self):
        """Log file is created with proper structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file)
            assert log_file.exists()
            data = json.loads(log_file.read_text())
            assert data["version"] == "1.0"
            assert "created" in data
            assert data["entries"] == []

    def test_init_creates_parent_directories(self):
        """Parent directories are created if they don't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "subdir" / "nested" / "log.json"
            log = ConsentLogSigned(log_file=log_file)
            assert log_file.exists()


# ---------------------------------------------------------------------------
# ConsentLogSigned - Signature Operations
# ---------------------------------------------------------------------------


class TestConsentLogSignedSignature:
    """HMAC-SHA256 signature computation and verification."""

    def test_compute_signature(self):
        """_compute_signature returns base64-encoded HMAC-SHA256."""
        key = b"secretkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            entry = ConsentEntry(
                user_id="user1",
                game="minecraft",
                timestamp="2026-06-30T10:00:00+00:00",
                opt_in_version="1.0",
            )
            sig = log._compute_signature(entry)
            # Should be valid base64
            decoded = base64.b64decode(sig)
            assert len(decoded) == 32  # SHA256 produces 32 bytes

    def test_signature_deterministic(self):
        """Same input produces same signature."""
        key = b"secretkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            entry = ConsentEntry(
                user_id="user1",
                game="minecraft",
                timestamp="2026-06-30T10:00:00+00:00",
                opt_in_version="1.0",
            )
            sig1 = log._compute_signature(entry)
            sig2 = log._compute_signature(entry)
            assert sig1 == sig2

    def test_signature_different_key_produces_different_result(self):
        """Different keys produce different signatures for same entry."""
        key1 = b"key1_1234567890123456"
        key2 = b"key2_1234567890123456"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file1 = Path(tmpdir) / "log1.json"
            log1 = ConsentLogSigned(log_file=log_file1, secret_key=key1)
            log_file2 = Path(tmpdir) / "log2.json"
            log2 = ConsentLogSigned(log_file=log_file2, secret_key=key2)
            entry = ConsentEntry(
                user_id="user1",
                game="minecraft",
                timestamp="2026-06-30T10:00:00+00:00",
                opt_in_version="1.0",
            )
            sig1 = log1._compute_signature(entry)
            sig2 = log2._compute_signature(entry)
            assert sig1 != sig2


# ---------------------------------------------------------------------------
# ConsentLogSigned - Entry Operations
# ---------------------------------------------------------------------------


class TestConsentLogSignedEntryOperations:
    """add_entry and verify_entry operations."""

    def test_add_entry(self):
        """add_entry creates signed entry and writes to log."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            entry = log.add_entry(user_id="user1", game="minecraft", opt_in_version="1.0")
            assert entry.user_id == "user1"
            assert entry.game == "minecraft"
            assert entry.signature is not None

            # Verify entry was written to log file
            data = json.loads(log_file.read_text())
            assert len(data["entries"]) == 1
            assert data["entries"][0]["user_id"] == "user1"

    def test_verify_entry_valid(self):
        """verify_entry returns True for valid signature."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            entry = log.add_entry(user_id="user1", game="minecraft")
            assert log.verify_entry(entry) is True

    def test_verify_entry_invalid_signature(self):
        """verify_entry returns False for tampered entry."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            entry = log.add_entry(user_id="user1", game="minecraft")
            # Tamper with the entry
            entry.user_id = "user_hacked"
            assert log.verify_entry(entry) is False

    def test_verify_entry_no_signature(self):
        """verify_entry returns False for entry without signature."""
        entry = ConsentEntry(user_id="user1", game="minecraft")
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            assert log.verify_entry(entry) is False

    def test_verify_all_valid(self):
        """verify_all returns valid count and empty invalid list."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            log.add_entry(user_id="user1", game="minecraft")
            log.add_entry(user_id="user2", game="factorio")
            valid_count, invalid = log.verify_all()
            assert valid_count == 2
            assert invalid == []

    def test_verify_all_with_invalid(self):
        """verify_all detects tampered entries."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            entry1 = log.add_entry(user_id="user1", game="minecraft")
            entry2 = log.add_entry(user_id="user2", game="factorio")
            # Tamper with entry2
            entry2.user_id = "hacked"
            # Write tampered entry back
            entries = [entry1, entry2]
            log._write_entries(entries)
            valid_count, invalid = log.verify_all()
            assert valid_count == 1
            assert len(invalid) == 1
            assert invalid[0].user_id == "hacked"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestConsentLogSignedCLI:
    """Command-line interface."""

    def test_cli_add_command(self):
        """add command creates entry and prints confirmation."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            key_file = Path(tmpdir) / "key.txt"
            key_file.write_text(base64.b64encode(key).decode())
            argv = [
                "-l", str(log_file), "-k", str(key_file),
                "add", "-u", "user1", "-g", "minecraft"
            ]
            exit_code = main(argv)
            assert exit_code == 0
            data = json.loads(log_file.read_text())
            assert len(data["entries"]) == 1
            assert data["entries"][0]["user_id"] == "user1"

    def test_cli_verify_command_valid(self):
        """verify command returns 0 when all entries valid."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            key_file = Path(tmpdir) / "key.txt"
            key_file.write_text(base64.b64encode(key).decode())
            # Add entry first
            argv = [
                "-l", str(log_file), "-k", str(key_file),
                "add", "-u", "user1", "-g", "minecraft"
            ]
            main(argv)
            # Verify
            argv = ["-l", str(log_file), "-k", str(key_file), "verify"]
            exit_code = main(argv)
            assert exit_code == 0

    def test_cli_verify_command_invalid(self):
        """verify command returns 1 when entries are invalid."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            key_file = Path(tmpdir) / "key.txt"
            key_file.write_text(base64.b64encode(key).decode())
            # Add entry
            argv = [
                "-l", str(log_file), "-k", str(key_file),
                "add", "-u", "user1", "-g", "minecraft"
            ]
            main(argv)
            # Tamper with log file
            data = json.loads(log_file.read_text())
            data["entries"][0]["user_id"] = "hacked"
            log_file.write_text(json.dumps(data))
            # Verify should fail
            argv = ["-l", str(log_file), "-k", str(key_file), "verify"]
            exit_code = main(argv)
            assert exit_code == 1

    def test_cli_list_command(self):
        """list command prints entries."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            key_file = Path(tmpdir) / "key.txt"
            key_file.write_text(base64.b64encode(key).decode())
            # Add entries
            main([
                "-l", str(log_file), "-k", str(key_file),
                "add", "-u", "user1", "-g", "minecraft"
            ])
            main([
                "-l", str(log_file), "-k", str(key_file),
                "add", "-u", "user2", "-g", "factorio"
            ])
            # List
            argv = ["-l", str(log_file), "-k", str(key_file), "list"]
            exit_code = main(argv)
            assert exit_code == 0

    def test_cli_list_filter_by_user(self):
        """list command filters by user_id."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            key_file = Path(tmpdir) / "key.txt"
            key_file.write_text(base64.b64encode(key).decode())
            main([
                "-l", str(log_file), "-k", str(key_file),
                "add", "-u", "user1", "-g", "minecraft"
            ])
            main([
                "-l", str(log_file), "-k", str(key_file),
                "add", "-u", "user2", "-g", "factorio"
            ])
            argv = ["-l", str(log_file), "-k", str(key_file), "list", "-u", "user1"]
            exit_code = main(argv)
            assert exit_code == 0

    def test_cli_list_filter_by_game(self):
        """list command filters by game."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            key_file = Path(tmpdir) / "key.txt"
            key_file.write_text(base64.b64encode(key).decode())
            main([
                "-l", str(log_file), "-k", str(key_file),
                "add", "-u", "user1", "-g", "minecraft"
            ])
            main([
                "-l", str(log_file), "-k", str(key_file),
                "add", "-u", "user2", "-g", "factorio"
            ])
            argv = ["-l", str(log_file), "-k", str(key_file), "list", "-g", "factorio"]
            exit_code = main(argv)
            assert exit_code == 0


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------


class TestConsentLogSignedErrorHandling:
    """Error handling and edge cases."""

    def test_read_invalid_json_raises(self):
        """Reading corrupted JSON raises ConsentLogError when entries are accessed."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log_file.write_text("not valid json {")
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            # Error is raised lazily when reading entries
            with pytest.raises(ConsentLogError, match="Invalid log file"):
                log._read_entries()

    def test_read_missing_entries_key(self):
        """Reading JSON without 'entries' key handles gracefully."""
        key = b"testkey1234567890"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            log_file.write_text(json.dumps({"version": "1.0"}))
            log = ConsentLogSigned(log_file=log_file, secret_key=key)
            entries = log._read_entries()
            assert entries == []

    def test_cli_missing_log_file(self):
        """CLI returns error when log file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "nonexistent.json"
            argv = ["-l", str(log_file), "list"]
            exit_code = main(argv)
            # Should return 1 or 2 for error - ConsentLogError or similar
            assert exit_code in (1, 2)

    def test_key_file_invalid_base64(self):
        """Key file with invalid base64 raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "log.json"
            key_file = Path(tmpdir) / "key.txt"
            key_file.write_text("not-valid-base64!!!")
            with pytest.raises(ConsentLogError, match="Failed to load key"):
                ConsentLogSigned(log_file=log_file, key_file=key_file)
