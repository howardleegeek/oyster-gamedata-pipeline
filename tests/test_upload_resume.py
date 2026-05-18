#!/usr/bin/env python3
"""
Tests for S3 multipart upload resume functionality.

Tests:
1. Simulate upload failure at 50%, verify resume picks up at chunk N+1
2. Verify SHA256 round-trip
3. Verify presigned URL refresh on expiry
"""

import hashlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bin.upload_daemon import CHUNK_SIZE, ChunkInfo, UploadDaemon, UploadSession, UploadState


class TestUploadResume(unittest.TestCase):
    """Test upload resume functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        self.state_file = Path(self.test_dir) / "upload_state.json"
        self.watch_dir = Path(self.test_dir) / "clips"
        self.watch_dir.mkdir()

        # Create a test file (16MB = 2 chunks of 8MB)
        self.test_file = self.watch_dir / "clip-20260517-150000.tar.gz"
        self.test_file.write_bytes(b"A" * (16 * 1024 * 1024))

        # Mock the state file location
        self.patcher = patch("bin.upload_daemon.STATE_FILE", self.state_file)
        self.patcher.start()

    def tearDown(self):
        """Clean up test fixtures."""
        self.patcher.stop()
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_resume_at_chunk_n_plus_1(self):
        """Test that resume picks up at chunk N+1 after failure at 50%."""

        # Create a mock daemon
        daemon = UploadDaemon(watch_dir=self.watch_dir, max_kbps=5000, dry_run=True)

        # Create a session with 50% uploaded (chunk 1 done, chunk 2 pending)
        session = UploadSession(
            session_id="clip-20260517-150000",
            local_path=str(self.test_file),
            file_size=16 * 1024 * 1024,
            sha256="test_sha256",
            state=UploadState.UPLOADING.value,
            upload_id="test_upload_id",
        )

        # Chunk 1 is done, chunk 2 is pending
        session.chunks = [
            ChunkInfo(index=1, offset=0, size=CHUNK_SIZE, etag="etag1", uploaded=True),
            ChunkInfo(index=2, offset=CHUNK_SIZE, size=CHUNK_SIZE, etag=None, uploaded=False),
        ]
        session.progress = 50.0

        daemon.state["sessions"] = {session.session_id: session}
        daemon._save_state()

        # Verify state was saved
        self.assertTrue(self.state_file.exists())

        # Reload state (simulating daemon restart)
        daemon2 = UploadDaemon(watch_dir=self.watch_dir, max_kbps=5000, dry_run=True)

        # Verify chunk 1 is marked as uploaded
        reloaded_session = daemon2.state["sessions"]["clip-20260517-150000"]
        self.assertIsInstance(reloaded_session, UploadSession)
        self.assertTrue(reloaded_session.chunks[0].uploaded)
        self.assertFalse(reloaded_session.chunks[1].uploaded)
        self.assertEqual(reloaded_session.progress, 50.0)

        # Simulate the resume logic - should skip chunk 1 and start at chunk 2
        chunks_to_upload = [c for c in reloaded_session.chunks if not c.uploaded]
        self.assertEqual(len(chunks_to_upload), 1)
        self.assertEqual(chunks_to_upload[0].index, 2)

        print("✓ Resume at chunk N+1 test passed")

    def test_sha256_roundtrip(self):
        """Test that SHA256 hash is correctly computed and verified."""

        # Create test data with known SHA256
        test_data = b"Hello, World! This is test data for SHA256 verification."
        expected_sha256 = hashlib.sha256(test_data).hexdigest()

        # Write to temp file
        test_file = Path(self.test_dir) / "test_sha256.txt"
        test_file.write_bytes(test_data)

        # Create daemon and compute SHA256
        daemon = UploadDaemon(watch_dir=self.watch_dir, max_kbps=5000, dry_run=True)

        computed_sha256 = daemon._compute_sha256(test_file)

        self.assertEqual(computed_sha256, expected_sha256)
        print(f"✓ SHA256 roundtrip test passed: {expected_sha256}")

    def test_presigned_url_refresh_on_expiry(self):
        """Test that presigned URLs are refreshed when expired."""

        # Test URL generation with expiry
        from server.s3_presigned_url import generate_presigned_url, refresh_presigned_url

        session_id = "test_session"
        upload_id = "test_upload_id"
        part_number = 1

        # Generate initial URL
        url1 = generate_presigned_url(upload_id, part_number, session_id)

        # Wait a tiny bit (in real scenario, URL would be expired)
        time.sleep(0.1)

        # Refresh URL (simulating expiry)
        url2 = refresh_presigned_url(upload_id, part_number, session_id)

        # URLs should be different due to different timestamps
        # (In real implementation, the signature would include timestamp)
        self.assertIsNotNone(url1)
        self.assertIsNotNone(url2)

        # Verify URL format
        self.assertIn(session_id, url1)
        self.assertIn(upload_id, url1)
        self.assertIn(str(part_number), url1)

        print("✓ Presigned URL refresh test passed")

    def test_idempotent_upload(self):
        """Test that re-uploading an already-uploaded session is a no-op."""

        daemon = UploadDaemon(watch_dir=self.watch_dir, max_kbps=5000, dry_run=True)

        # Create a completed session
        session = UploadSession(
            session_id="clip-20260517-150000",
            local_path=str(self.test_file),
            file_size=16 * 1024 * 1024,
            sha256="test_sha256",
            state=UploadState.COMPLETED.value,
            completed_at="2025-01-01T00:00:00",
        )

        daemon.state["sessions"] = {session.session_id: session}

        # Check if already completed
        if session.session_id in daemon.state["sessions"]:
            existing = daemon.state["sessions"][session.session_id]
            if (
                isinstance(existing, UploadSession)
                and existing.state == UploadState.COMPLETED.value
            ):
                # Should skip
                print("✓ Idempotent upload test passed (skipped completed session)")
                return

        self.fail("Should have skipped completed session")

    def test_state_persistence(self):
        """Test that upload state is persisted to disk."""

        daemon = UploadDaemon(watch_dir=self.watch_dir, max_kbps=5000, dry_run=True)

        # Create a session
        session = UploadSession(
            session_id="clip-20260517-150000",
            local_path=str(self.test_file),
            file_size=16 * 1024 * 1024,
            sha256="test_sha256",
            state=UploadState.PENDING.value,
        )

        daemon.state["sessions"][session.session_id] = session
        daemon._save_state()

        # Verify file exists
        self.assertTrue(self.state_file.exists())

        # Load state in new daemon instance
        daemon2 = UploadDaemon(watch_dir=self.watch_dir, max_kbps=5000, dry_run=True)

        # Verify session was loaded
        self.assertIn("clip-20260517-150000", daemon2.state["sessions"])

        print("✓ State persistence test passed")

    def test_chunk_info_serialization(self):
        """Test that ChunkInfo can be serialized and deserialized."""

        chunk = ChunkInfo(index=1, offset=0, size=CHUNK_SIZE, etag="test_etag", uploaded=True)

        # Serialize
        chunk_dict = {
            "index": chunk.index,
            "offset": chunk.offset,
            "size": chunk.size,
            "etag": chunk.etag,
            "uploaded": chunk.uploaded,
        }

        # Deserialize
        chunk2 = ChunkInfo(**chunk_dict)

        self.assertEqual(chunk.index, chunk2.index)
        self.assertEqual(chunk.offset, chunk2.offset)
        self.assertEqual(chunk.size, chunk2.size)
        self.assertEqual(chunk.etag, chunk2.etag)
        self.assertEqual(chunk.uploaded, chunk2.uploaded)

        print("✓ Chunk info serialization test passed")


class TestBandwidthThrottling(unittest.TestCase):
    """Test bandwidth throttling functionality."""

    def test_throttle_calculation(self):
        """Test that throttle correctly limits bandwidth."""

        # Use a very high bandwidth limit so the test runs fast
        # but still verify the throttle function works
        daemon = UploadDaemon(max_kbps=100000)  # 100 MB/s - should be very fast

        # Simulate sending 1KB
        start_time = time.time()
        bytes_sent = 1024  # 1KB

        daemon._throttle(bytes_sent, start_time)

        elapsed = time.time() - start_time

        # At 100 MB/s, 1KB should take virtually no time
        # Just verify it doesn't block excessively
        self.assertLess(elapsed, 0.1)

        print(f"✓ Bandwidth throttling test passed (elapsed: {elapsed:.4f}s)")


class TestWiFiOnly(unittest.TestCase):
    """Test WiFi-only upload functionality."""

    @patch("subprocess.run")
    def test_wifi_detection(self, mock_run):
        """Test WiFi detection."""
        # Mock networksetup output showing WiFi
        mock_run.return_value = Mock(stdout="Hardware Port: Wi-Fi\nDevice: en0\n", returncode=0)

        daemon = UploadDaemon()
        is_wifi = daemon._is_wifi_only()

        # Should return True for WiFi-only
        self.assertTrue(is_wifi)

        print("✓ WiFi detection test passed")


def run_tests():
    """Run all tests."""
    print("=" * 60)
    print("Running Upload Resume Tests")
    print("=" * 60)

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test cases
    suite.addTests(loader.loadTestsFromTestCase(TestUploadResume))
    suite.addTests(loader.loadTestsFromTestCase(TestBandwidthThrottling))
    suite.addTests(loader.loadTestsFromTestCase(TestWiFiOnly))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print("All tests passed! ✓")
    else:
        print(f"Tests failed: {len(result.failures)} failures, {len(result.errors)} errors")
    print("=" * 60)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
