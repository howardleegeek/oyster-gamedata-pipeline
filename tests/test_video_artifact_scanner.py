#!/usr/bin/env python3
"""
Tests for video artifact scanner.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Add bin directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

from video_artifact_scanner import (
    calculate_verdict,
    compute_dhash,
    detect_freezes,
    detect_stutters,
    find_video_file,
    hamming_distance,
    scan_video_artifacts,
)


class TestVideoArtifactScanner(unittest.TestCase):
    """Test cases for video artifact scanner."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        self.session_dir = os.path.join(self.test_dir, "test_session")
        os.makedirs(self.session_dir, exist_ok=True)

    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.test_dir)

    def test_find_video_file_no_mp4(self):
        """Test finding video file when no MP4 exists."""
        # Create empty session directory
        empty_dir = os.path.join(self.test_dir, "empty_session")
        os.makedirs(empty_dir, exist_ok=True)

        result = find_video_file(empty_dir)
        self.assertIsNone(result)

    def test_find_video_file_with_mp4(self):
        """Test finding video file when MP4 exists."""
        # Create a dummy MP4 file
        mp4_path = os.path.join(self.session_dir, "test_video.mp4")
        with open(mp4_path, "wb") as f:
            f.write(b"fake mp4 data")

        result = find_video_file(self.session_dir)
        self.assertEqual(result, mp4_path)

    def test_compute_dhash(self):
        """Test dHash computation."""
        # Create a simple 8x8 gradient thumbnail
        thumbnail = bytearray()
        for i in range(8):
            for j in range(8):
                # Create a gradient: brighter on right side
                thumbnail.append(j * 32)

        dhash = compute_dhash(bytes(thumbnail))

        # Check that hash is 14 hex characters (56 bits)
        self.assertEqual(len(dhash), 14)
        self.assertTrue(all(c in "0123456789abcdef" for c in dhash))

        # Test with wrong size
        with self.assertRaises(ValueError):
            compute_dhash(b"short")

    def test_hamming_distance(self):
        """Test Hamming distance calculation."""
        # Same hash should have distance 0
        hash1 = "0123456789abcd"
        self.assertEqual(hamming_distance(hash1, hash1), 0)

        # Different hashes should have positive distance
        hash2 = "0123456789abce"  # Last nibble different
        distance = hamming_distance(hash1, hash2)
        self.assertGreater(distance, 0)
        self.assertLessEqual(distance, 56)  # Max 56 bits

        # Test with all bits different
        hash3 = "ffffffffffffff"
        hash4 = "00000000000000"
        self.assertEqual(hamming_distance(hash3, hash4), 56)

    def test_detect_freezes(self):
        """Test freeze detection."""
        # Create hashes with freezes of different lengths
        hashes = []

        # 2 identical hashes (20 frames) - NOT a freeze (< 30 frames)
        hashes.extend(["a" * 14] * 2)

        # 5 different hashes (all different from each other)
        for i in range(5):
            hashes.append(f"b{i:013x}")  # Different hash for each

        # 4 identical hashes (40 frames) - IS a freeze (≥ 30 frames)
        hashes.extend(["c" * 14] * 4)

        # 3 different hashes (all different from each other)
        for i in range(3):
            hashes.append(f"d{i:013x}")  # Different hash for each

        # 3 identical hashes (30 frames) - IS a freeze (≥ 30 frames)
        hashes.extend(["e" * 14] * 3)

        freezes = detect_freezes(hashes, sample_rate=10)

        # Should detect 2 freezes: the 4 "c" hashes and the 3 "e" hashes
        # The 2 "a" hashes are too short (20 frames < 30)
        # The "b" and "d" runs have different hashes, so no freeze
        self.assertEqual(len(freezes), 2)

        # Check first freeze (4 "c" hashes)
        # Position: 2 "a" + 5 different "b" = 7 hashes before
        # "c" hashes are at indices 7, 8, 9, 10
        self.assertEqual(freezes[0]["start_frame"], 70)  # 7 × 10
        self.assertEqual(freezes[0]["end_frame"], 100)  # 10 × 10
        self.assertAlmostEqual(freezes[0]["duration_s"], 40 / 60.0)  # 40 frames / 60fps

        # Check second freeze (3 "e" hashes)
        # Position: 2 "a" + 5 "b" + 4 "c" + 3 "d" = 14 hashes before
        # "e" hashes are at indices 14, 15, 16
        self.assertEqual(freezes[1]["start_frame"], 140)  # 14 × 10
        self.assertEqual(freezes[1]["end_frame"], 160)  # 16 × 10
        self.assertAlmostEqual(freezes[1]["duration_s"], 30 / 60.0)  # 30 frames / 60fps

    def test_detect_stutters(self):
        """Test stutter detection."""
        # Create hashes with a stutter pattern
        hashes = []

        # Normal sequence: small changes (distance < 5)
        # Use hashes that are similar
        hashes.append("00000000000000")
        hashes.append("00000000000001")  # Distance 1 from previous
        hashes.append("00000000000003")  # Distance 2 from previous

        # Stutter: small change then big change
        hashes.append("00000000000007")  # Distance 2 from previous (< 5)
        hashes.append("ffffffffffffff")  # Distance 56 from previous (> 20)

        # Continue with normal sequence
        hashes.append("fffffffffffffe")  # Distance 1 from previous
        hashes.append("fffffffffffffc")  # Distance 2 from previous

        stutters = detect_stutters(hashes, sample_rate=10)

        # Should detect the stutter at position 4 (index 4 in 0-based)
        self.assertEqual(len(stutters), 1)
        self.assertEqual(stutters[0]["frame"], 40)  # 4th sampled frame × 10
        # hamming_jump should be "2->56" or similar

    def test_calculate_verdict(self):
        """Test verdict calculation."""
        # Test PASS (ratio < 0.001)
        freezes = [{"start_frame": 0, "end_frame": 29, "duration_s": 0.5}]  # 30 frames
        stutters = []
        total_frames = 60000  # 1000 seconds at 60fps
        verdict, ratio = calculate_verdict(freezes, stutters, total_frames)
        self.assertEqual(verdict, "PASS")
        self.assertAlmostEqual(ratio, 30 / 60000)  # 0.0005

        # Test PASS_DEGRADED (0.001 ≤ ratio < 0.01)
        freezes = [{"start_frame": 0, "end_frame": 299, "duration_s": 5.0}]  # 300 frames
        verdict, ratio = calculate_verdict(freezes, stutters, total_frames)
        self.assertEqual(verdict, "PASS_DEGRADED")
        self.assertAlmostEqual(ratio, 300 / 60000)  # 0.005

        # Test FAIL (ratio ≥ 0.01)
        freezes = [{"start_frame": 0, "end_frame": 599, "duration_s": 10.0}]  # 600 frames
        verdict, ratio = calculate_verdict(freezes, stutters, total_frames)
        self.assertEqual(verdict, "FAIL")
        self.assertAlmostEqual(ratio, 600 / 60000)  # 0.01

        # Test with stutters (count as 5 frames each)
        freezes = []
        stutters = [{"frame": 0}, {"frame": 1000}]
        verdict, ratio = calculate_verdict(freezes, stutters, total_frames)
        self.assertEqual(verdict, "PASS")
        self.assertAlmostEqual(ratio, 10 / 60000)  # 0.000167

        # Test mixed artifacts
        freezes = [{"start_frame": 0, "end_frame": 149, "duration_s": 2.5}]  # 150 frames
        stutters = [{"frame": 0}, {"frame": 1000}]  # 2 stutters = 10 frames
        verdict, ratio = calculate_verdict(freezes, stutters, total_frames)
        self.assertEqual(verdict, "PASS_DEGRADED")
        self.assertAlmostEqual(ratio, 160 / 60000)  # 0.002667

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
    def test_create_test_video_with_freeze(self):
        """Create a test video with freeze frames using ffmpeg."""
        # Create a simple test video with ffmpeg
        test_video_path = os.path.join(self.session_dir, "test_freeze.mp4")

        # Create a simple color test pattern
        cmd = [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=640x480:duration=5",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=640x480:duration=5",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1",
            "-c:v",
            "libx264",
            "-t",
            "10",  # 10 second video
            test_video_path,
        ]

        try:
            subprocess.run(cmd, capture_output=True, check=True)
            self.assertTrue(os.path.exists(test_video_path))
        except subprocess.CalledProcessError:
            self.skipTest("Failed to create test video")

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not available"
    )
    def test_integration_scan(self):
        """Test integration scanning with actual video."""
        # Create a simple test video
        test_video_path = os.path.join(self.session_dir, "test_scan.mp4")

        # Create a 2-second test video with solid color
        cmd = [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:size=320x240:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            test_video_path,
        ]

        try:
            subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError:
            self.skipTest("Failed to create test video")

        # Test scanning
        result = scan_video_artifacts(test_video_path, sample_rate=10)

        # Check basic structure
        self.assertIn("file", result)
        self.assertIn("duration_s", result)
        self.assertIn("frames_sampled", result)
        self.assertIn("freeze_events", result)
        self.assertIn("stutter_events", result)
        self.assertIn("verdict", result)

        # Should have some frames sampled
        self.assertGreater(result["frames_sampled"], 0)

        # Duration should be around 2 seconds
        self.assertGreaterEqual(result["duration_s"], 1.5)
        self.assertLessEqual(result["duration_s"], 2.5)

    def test_cli_no_mp4(self):
        """Test CLI with no MP4 file."""
        # Create empty session directory
        empty_dir = os.path.join(self.test_dir, "empty_session")
        os.makedirs(empty_dir, exist_ok=True)

        # Run CLI
        cmd = [sys.executable, "bin/video_artifact_scanner.py", empty_dir]
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Should exit with 0 (SKIP)
        self.assertEqual(result.returncode, 0)
        self.assertIn("No MP4 file found", result.stdout)

    def test_cli_json_output(self):
        """Test CLI JSON output format."""
        # Create dummy MP4 file
        mp4_path = os.path.join(self.session_dir, "dummy.mp4")
        with open(mp4_path, "wb") as f:
            f.write(b"fake")

        # Run CLI with --json
        cmd = [sys.executable, "bin/video_artifact_scanner.py", self.session_dir, "--json"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Should exit with 0 (SKIP due to invalid video)
        self.assertEqual(result.returncode, 0)

        # Should output valid JSON
        try:
            output = json.loads(result.stdout)
            self.assertIn("verdict", output)
            self.assertEqual(output["verdict"], "SKIP")
        except json.JSONDecodeError:
            self.fail("Output is not valid JSON")


if __name__ == "__main__":
    unittest.main()
