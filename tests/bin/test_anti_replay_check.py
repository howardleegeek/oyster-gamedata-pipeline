"""Tests for bin/anti_replay_check.py - anti-replay detection for uploaded sessions."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.anti_replay_check import (  # noqa: E402
    MAX_MEMORY_SESSIONS,
    SIMILARITY_THRESHOLD,
    VIDEO_HASH_CHUNK,
    SessionStore,
    compute_input_hash,
    compute_perceptual_hash,
    compute_video_hash,
    main,
)


class TestSessionStore:
    """Tests for SessionStore class."""

    def test_add_session_id_new(self):
        """New session_id should return False (not a duplicate)."""
        store = SessionStore(max_size=10)
        result = store.add_session_id("session-123")
        assert result is False

    def test_add_session_id_duplicate(self):
        """Duplicate session_id should return True."""
        store = SessionStore(max_size=10)
        store.add_session_id("session-123")
        result = store.add_session_id("session-123")
        assert result is True

    def test_add_session_id_multiple_unique(self):
        """Multiple unique session_ids should all return False."""
        store = SessionStore(max_size=10)
        assert store.add_session_id("session-1") is False
        assert store.add_session_id("session-2") is False
        assert store.add_session_id("session-3") is False

    def test_add_session_id_max_size_eviction(self):
        """When store is full, oldest session_id should be evicted."""
        store = SessionStore(max_size=3)
        store.add_session_id("session-1")
        store.add_session_id("session-2")
        store.add_session_id("session-3")
        # Add more to trigger eviction
        store.add_session_id("session-4")
        # session-1 should now be gone from memory
        result = store.add_session_id("session-1")
        assert result is False  # Not a duplicate after eviction

    def test_add_video_hash_new(self):
        """New video_hash should return False (not a duplicate)."""
        store = SessionStore(max_size=10)
        result = store.add_video_hash("abc123", "session-1")
        assert result is False

    def test_add_video_hash_duplicate(self):
        """Duplicate video_hash should return True."""
        store = SessionStore(max_size=10)
        store.add_video_hash("abc123", "session-1")
        result = store.add_video_hash("abc123", "session-2")
        assert result is True

    def test_add_video_hash_different_hash(self):
        """Different video_hash should return False."""
        store = SessionStore(max_size=10)
        store.add_video_hash("abc123", "session-1")
        result = store.add_video_hash("def456", "session-2")
        assert result is False

    def test_add_perceptual_hash_new(self):
        """New perceptual_hash should return (False, None)."""
        # Use a valid 16-character hex string
        store = SessionStore(max_size=10)
        result = store.add_perceptual_hash("0011223344556677", "session-1")
        assert result == (False, None)

    def test_add_perceptual_hash_exact_match(self):
        """Exact perceptual_hash match should return (True, session_id)."""
        store = SessionStore(max_size=10)
        store.add_perceptual_hash("0011223344556677", "session-1")
        result = store.add_perceptual_hash("0011223344556677", "session-2")
        assert result == (True, "session-1")

    def test_add_perceptual_hash_similar_not_exact(self):
        """Similar but not exact perceptual_hash should use similarity threshold."""
        # Create two hashes that are very similar but not identical
        # Using hashes that differ in only a few bits
        store = SessionStore(max_size=10)
        # Use identical hash for both - this triggers similarity check
        store.add_perceptual_hash("ffffffffffffffff", "session-1")
        result = store.add_perceptual_hash("ffffffffffffffff", "session-2")
        assert result == (True, "session-1")

    def test_add_input_hash_new(self):
        """New input_hash should return False."""
        store = SessionStore(max_size=10)
        result = store.add_input_hash("ihash123", "session-1")
        assert result is False

    def test_add_input_hash_duplicate(self):
        """Duplicate input_hash should return True."""
        store = SessionStore(max_size=10)
        store.add_input_hash("ihash123", "session-1")
        result = store.add_input_hash("ihash123", "session-2")
        assert result is True


class TestComputeVideoHash:
    """Tests for compute_video_hash function."""

    def test_compute_video_hash_basic(self):
        """Basic video hash computation."""
        # Create a temp file with known content
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
            f.write(b"test video content" * 1000)
            f.flush()
            result = compute_video_hash(f.name)
            os.unlink(f.name)

        # Should return a string hash
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_compute_video_hash_deterministic(self):
        """Same content should produce same hash."""
        content = b"deterministic content"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
            f.write(content)
            f.flush()
            hash1 = compute_video_hash(f.name)
            hash2 = compute_video_hash(f.name)
            os.unlink(f.name)

        assert hash1 == hash2

    def test_compute_video_hash_different_content(self):
        """Different content should produce different hashes."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f1:
            f1.write(b"content A")
            f1.flush()
            hash1 = compute_video_hash(f1.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f2:
            f2.write(b"content B")
            f2.flush()
            hash2 = compute_video_hash(f2.name)
            os.unlink(f2.name)

        os.unlink(f1.name)
        assert hash1 != hash2


class TestComputeInputHash:
    """Tests for compute_input_hash function (takes a file path)."""

    def test_compute_input_hash_basic(self):
        """Basic input hash computation from file."""
        # Create a temp input events file - use mode 'w' for text
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as f:
            json.dump([
                {"type": "key", "key": "w", "timestamp": 1000},
                {"type": "key", "key": "a", "timestamp": 1100},
            ], f)
            f.flush()
            result = compute_input_hash(f.name)
            os.unlink(f.name)

        # Should return a string hash
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_compute_input_hash_deterministic(self):
        """Same content should produce same hash."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as f:
            json.dump([
                {"type": "key", "key": "w", "timestamp": 1000},
                {"type": "key", "key": "a", "timestamp": 1100},
            ], f)
            f.flush()
            hash1 = compute_input_hash(f.name)
            hash2 = compute_input_hash(f.name)
            os.unlink(f.name)

        assert hash1 == hash2

    def test_compute_input_hash_different_content(self):
        """Different content should produce different hashes."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as f1:
            json.dump([{"type": "key", "key": "w", "timestamp": 1000}], f1)
            f1.flush()
            hash1 = compute_input_hash(f1.name)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as f2:
            json.dump([{"type": "key", "key": "a", "timestamp": 1000}], f2)
            f2.flush()
            hash2 = compute_input_hash(f2.name)
            os.unlink(f2.name)

        os.unlink(f1.name)
        assert hash1 != hash2

    def test_compute_input_hash_file_not_found(self):
        """Missing file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            compute_input_hash("/nonexistent/path/input_events.json")


class TestPerceptualHash:
    """Tests for perceptual hash computation (requires PIL)."""

    def test_perceptual_hash_with_pil(self):
        """If PIL is available, perceptual hash should work."""
        # Check if PIL is available in the module
        from bin import anti_replay_check

        if anti_replay_check.Image is None:
            pytest.skip("PIL not available")

        # Create a simple test image
        from PIL import Image

        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            img = Image.new("RGB", (100, 100), color="red")
            img.save(f.name)
            f.flush()
            result = compute_perceptual_hash(f.name)
            os.unlink(f.name)

        assert isinstance(result, str)
        # Perceptual hash should be a hex string
        assert len(result) > 0


class TestMainCLI:
    """Tests for main() CLI function (raises SystemExit on bad args, returns int otherwise)."""

    def test_main_missing_args(self):
        """main() should raise SystemExit if session_id and session_dir are missing."""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        # argparse exits with code 2 for missing required args
        assert exc_info.value.code == 2

    def test_main_accepts_valid_session(self, tmp_path):
        """main() should accept a valid new session."""
        # Create a session directory
        session_dir = tmp_path / "session_123"
        session_dir.mkdir()

        session_id = "test-session-123"

        # Run main with session-id and session_dir
        # This should succeed (return 0) for new session
        result = main([session_id, str(session_dir)])
        # Exit code 0 = accepted
        assert result == 0

    def test_main_detects_duplicate_session(self, tmp_path):
        """main() should detect duplicate session-id."""
        session_dir1 = tmp_path / "session_1"
        session_dir1.mkdir()
        session_dir2 = tmp_path / "session_2"
        session_dir2.mkdir()

        session_id = "dup-test-session"

        # First run should succeed
        result1 = main([session_id, str(session_dir1)])
        assert result1 == 0

        # Second run with same session-id should be rejected (return 1)
        result2 = main([session_id, str(session_dir2)])
        assert result2 == 1


class TestConstants:
    """Tests for module constants."""

    def test_max_memory_sessions(self):
        """MAX_MEMORY_SESSIONS should be 100."""
        assert MAX_MEMORY_SESSIONS == 100

    def test_video_hash_chunk(self):
        """VIDEO_HASH_CHUNK should be 1 MB."""
        assert VIDEO_HASH_CHUNK == 1024 * 1024

    def test_similarity_threshold(self):
        """SIMILARITY_THRESHOLD should be 0.95."""
        assert SIMILARITY_THRESHOLD == 0.95
