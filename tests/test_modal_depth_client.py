"""
Mock-server smoke test for the Modal depth client.

Spins up a local mock HTTP server that returns 1800 dummy EXRs in a tar.gz,
then verifies the client can:
  - Upload mp4 successfully
  - Parse tar.gz response
  - Place EXRs in correct directory
  - Write .source marker with kind: server_da_v2
"""

import glob
import io
import os
import shutil
import struct
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def create_dummy_exr_bytes(width=2, height=2):
    """Create a minimal valid EXR file in memory (2x2 pixels for speed)."""
    # EXR magic number
    magic = struct.pack("<I", 20000630)

    # Version: scanline, single part
    version = struct.pack("<I", 2)

    # Build header
    header_data = b""

    # channels attribute
    channels_data = b""
    for ch_name in ["Z"]:
        channels_data += ch_name.encode("utf-8") + b"\x00"
        channels_data += struct.pack("<i", 1)  # pixel type FLOAT
        channels_data += struct.pack("<i", 1)  # pLinear
        channels_data += struct.pack("<i", 0)  # xSampling
        channels_data += struct.pack("<i", 0)  # ySampling

    header_data += b"channels\x00"
    header_data += b"chlist\x00"
    header_data += struct.pack("<i", len(channels_data) + 1)
    header_data += channels_data
    header_data += b"\x00"

    # compression (none = 0)
    header_data += b"compression\x00"
    header_data += b"compression\x00"
    header_data += struct.pack("<i", 1)
    header_data += struct.pack("<B", 0)

    # dataWindow
    header_data += b"dataWindow\x00"
    header_data += b"box2i\x00"
    header_data += struct.pack("<i", 16)
    header_data += struct.pack("<iiii", 0, 0, width - 1, height - 1)

    # displayWindow
    header_data += b"displayWindow\x00"
    header_data += b"box2i\x00"
    header_data += struct.pack("<i", 16)
    header_data += struct.pack("<iiii", 0, 0, width - 1, height - 1)

    # lineOrder
    header_data += b"lineOrder\x00"
    header_data += b"lineOrder\x00"
    header_data += struct.pack("<i", 1)
    header_data += struct.pack("<B", 0)

    # screenWindowCenter
    header_data += b"screenWindowCenter\x00"
    header_data += b"v2f\x00"
    header_data += struct.pack("<i", 8)
    header_data += struct.pack("<ff", 0.0, 0.0)

    # screenWindowWidth
    header_data += b"screenWindowWidth\x00"
    header_data += b"float\x00"
    header_data += struct.pack("<i", 4)
    header_data += struct.pack("<f", 1.0)

    # End of header
    header_data += b"\x00"

    # Scanline offsets table
    scanline_size = width * 4  # 1 float per pixel
    offsets = []
    current_offset = len(magic) + len(version) + len(header_data) + height * 8
    for y in range(height):
        offsets.append(struct.pack("<q", current_offset))
        current_offset += 4 + 4 + scanline_size

    # Scanline data
    scanline_blocks = b""
    for y in range(height):
        scanline_blocks += struct.pack("<i", scanline_size)  # packed size
        scanline_blocks += struct.pack("<i", scanline_size)  # uncompressed size
        for x in range(width):
            scanline_blocks += struct.pack("<f", 0.5)

    return magic + version + header_data + b"".join(offsets) + scanline_blocks


def create_mock_tar_gz(num_exrs=1800):
    """Create a tar.gz containing num_exrs dummy EXR files."""
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        for i in range(num_exrs):
            exr_bytes = create_dummy_exr_bytes()
            info = tarfile.TarInfo(name=f"depth/frame_{i:06d}.exr")
            info.size = len(exr_bytes)
            info.mtime = time.time()
            tar.addfile(info, io.BytesIO(exr_bytes))
    tar_buffer.seek(0)
    return tar_buffer.getvalue()


class MockDepthHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler that returns dummy depth EXRs."""

    def do_POST(self):
        """Handle POST requests by returning a tar.gz of dummy EXRs."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            self.rfile.read(content_length)

        tar_bytes = create_mock_tar_gz(num_exrs=1800)

        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Disposition", "attachment; filename=depth.tar.gz")
        self.send_header("Content-Length", str(len(tar_bytes)))
        self.end_headers()
        self.wfile.write(tar_bytes)

    def do_GET(self):
        """Handle GET requests (health check)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        """Suppress log messages during tests."""
        pass


class TestModalDepthClient(unittest.TestCase):
    """Smoke test for the Modal depth client."""

    @classmethod
    def setUpClass(cls):
        """Start the mock server."""
        cls.server = HTTPServer(("127.0.0.1", 0), MockDepthHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever)
        cls.server_thread.daemon = True
        cls.server_thread.start()
        cls.endpoint = f"http://127.0.0.1:{cls.port}"
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        """Stop the mock server."""
        cls.server.shutdown()

    def setUp(self):
        """Create temporary directories for each test."""
        self.tmpdir = tempfile.mkdtemp()
        self.frames_dir = os.path.join(self.tmpdir, "frames")
        self.depth_dir = os.path.join(self.tmpdir, "depth")
        os.makedirs(self.frames_dir, exist_ok=True)
        os.makedirs(self.depth_dir, exist_ok=True)

        # Create dummy frames
        for i in range(10):
            frame_path = os.path.join(self.frames_dir, f"frame_{i:06d}.jpg")
            with open(frame_path, "wb") as f:
                # Minimal valid JPEG
                f.write(
                    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
                    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
                    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00"
                    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\xff\xd9"
                )

    def tearDown(self):
        """Clean up temporary directories."""
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_upload_mp4_successfully(self):
        """Test that the client can upload video and receive a response."""
        from bin.run_da_v2_depth_remote import upload_and_compute

        dummy_video = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41"

        tar_bytes = upload_and_compute(
            dummy_video,
            self.endpoint,
            auth_token="",
            fps=6,
            timeout=30,
        )

        self.assertIsNotNone(tar_bytes)
        self.assertGreater(len(tar_bytes), 0)

    def test_parse_tar_gz_response(self):
        """Test that the client can parse the tar.gz response."""
        from bin.run_da_v2_depth_remote import extract_depth_exrs, upload_and_compute

        dummy_video = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41"

        tar_bytes = upload_and_compute(
            dummy_video,
            self.endpoint,
            auth_token="",
            fps=6,
            timeout=30,
        )

        exr_files = extract_depth_exrs(tar_bytes, self.depth_dir)

        # Should have 1800 EXR files
        self.assertEqual(len(exr_files), 1800)

        # Verify files are in the correct directory
        for exr_file in exr_files:
            self.assertTrue(os.path.exists(exr_file))
            self.assertTrue(exr_file.endswith(".exr"))

    def test_exrs_in_correct_directory(self):
        """Test that EXRs are placed in the correct directory."""
        from bin.run_da_v2_depth_remote import extract_depth_exrs, upload_and_compute

        dummy_video = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41"

        tar_bytes = upload_and_compute(
            dummy_video,
            self.endpoint,
            auth_token="",
            fps=6,
            timeout=30,
        )

        extract_depth_exrs(tar_bytes, self.depth_dir)

        # Check that files are directly in depth_dir, not in a subdirectory
        exr_files = glob.glob(os.path.join(self.depth_dir, "*.exr"))
        self.assertEqual(len(exr_files), 1800)

        # Verify no subdirectories were created
        subdirs = [
            d for d in os.listdir(self.depth_dir) if os.path.isdir(os.path.join(self.depth_dir, d))
        ]
        self.assertEqual(len(subdirs), 0, f"Unexpected subdirectories: {subdirs}")

    def test_write_source_marker(self):
        """Test that the .source marker is written correctly."""
        from bin.run_da_v2_depth_remote import write_source_marker

        write_source_marker(self.depth_dir)

        source_path = os.path.join(self.depth_dir, ".source")
        self.assertTrue(os.path.exists(source_path))

        with open(source_path, "r") as f:
            content = f.read()

        self.assertIn("kind: server_da_v2", content)
        self.assertIn("timestamp:", content)
        self.assertIn("model: depth-anything-v2-small", content)
        self.assertIn("compute: modal-a10g", content)

    def test_full_pipeline(self):
        """Test the full pipeline: upload -> parse -> extract -> marker."""
        from bin.run_da_v2_depth_remote import (
            extract_depth_exrs,
            upload_and_compute,
            write_source_marker,
        )

        dummy_video = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41"

        # Upload and compute
        tar_bytes = upload_and_compute(
            dummy_video,
            self.endpoint,
            auth_token="",
            fps=6,
            timeout=30,
        )
        self.assertIsNotNone(tar_bytes)

        # Extract
        exr_files = extract_depth_exrs(tar_bytes, self.depth_dir)
        self.assertEqual(len(exr_files), 1800)

        # Write marker
        write_source_marker(self.depth_dir)

        # Verify marker
        source_path = os.path.join(self.depth_dir, ".source")
        self.assertTrue(os.path.exists(source_path))
        with open(source_path, "r") as f:
            self.assertIn("kind: server_da_v2", f.read())

    def test_skip_depth_fallback(self):
        """Test that --skip-depth creates the depth dir and marker."""
        from bin.run_da_v2_depth_remote import write_source_marker

        # Simulate skip-depth behavior
        os.makedirs(self.depth_dir, exist_ok=True)
        write_source_marker(self.depth_dir)

        source_path = os.path.join(self.depth_dir, ".source")
        self.assertTrue(os.path.exists(source_path))

        with open(source_path, "r") as f:
            content = f.read()

        self.assertIn("kind: server_da_v2", content)


if __name__ == "__main__":
    unittest.main()
