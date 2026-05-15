#!/usr/bin/env python3
"""Tests for bin/prd_test_video_no_ui.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_video_no_ui.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _create_test_video(tmpdir: Path, num_frames: int = 30) -> Path:
    """Create a minimal test video file using ffmpeg if available."""
    video_path = tmpdir / "test_video.mp4"
    
    # Create a simple test pattern using ffmpeg
    # Generate a solid color video with no text/UI
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=640x480:d={num_frames/30}",
        "-c:v", "libx264", "-t", f"{num_frames/30}", "-pix_fmt", "yuv420p",
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    
    if result.returncode != 0:
        # If ffmpeg not available, create a dummy file
        video_path.write_text("dummy video")
    
    return video_path


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------

class TestUIKeywords:
    """Tests for UI keyword detection."""

    def test_ui_keywords_defined(self):
        """UI keywords should be defined."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("video_no_ui", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        
        assert hasattr(mod, '_UI_KEYWORDS')
        assert "chat" in mod._UI_KEYWORDS
        assert "dialog" in mod._UI_KEYWORDS
        assert "TOP_BAR" in mod._UI_KEYWORDS
        assert "BOTTOM_BAR" in mod._UI_KEYWORDS


class TestHeuristicOCR:
    """Tests for heuristic OCR function."""

    def _heuristic(self, image):
        import importlib.util
        spec = importlib.util.spec_from_file_location("video_no_ui", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._heuristic_ocr(image)

    def test_heuristic_returns_string(self):
        """Heuristic OCR should return a string."""
        from PIL import Image
        
        # Create a simple test image (solid gray)
        img = Image.new('L', (640, 480), 128)
        result = self._heuristic(img)
        
        assert isinstance(result, str)

    def test_heuristic_detects_top_bar(self):
        """Heuristic should detect uniform top bar region."""
        from PIL import Image
        
        # Create image with uniform top bar (low std dev, distinct from center)
        img = Image.new('L', (640, 480), 128)
        # Draw a bright bar in the top 8%
        for y in range(int(480 * 0.08)):
            for x in range(640):
                img.putpixel((x, y), 200)
        
        result = self._heuristic(img)
        assert "TOP_BAR" in result

    def test_heuristic_detects_bottom_bar(self):
        """Heuristic should detect uniform bottom bar region."""
        from PIL import Image
        
        # Create image with uniform bottom bar (distinct from center)
        img = Image.new('L', (640, 480), 128)
        # Draw a bright bar in the bottom 8%
        for y in range(int(480 * 0.92), 480):
            for x in range(640):
                img.putpixel((x, y), 200)
        
        result = self._heuristic(img)
        assert "BOTTOM_BAR" in result


class TestSampleIndices:
    """Tests for frame sampling."""

    def _sample(self, total: int, n: int):
        import importlib.util
        spec = importlib.util.spec_from_file_location("video_no_ui", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._sample_indices(total, n)

    def test_sample_fewer_frames(self):
        """When total <= n, return all indices."""
        result = self._sample(5, 10)
        assert result == [0, 1, 2, 3, 4]

    def test_sample_even_spacing(self):
        """Sample should be evenly spaced."""
        result = self._sample(100, 10)
        assert len(result) == 10
        assert result[0] == 0
        # Check roughly even spacing
        step = result[1] - result[0]
        assert step > 0

    def test_sample_single_frame(self):
        """Single frame should return [0]."""
        result = self._sample(1, 10)
        assert result == [0]


class TestOCREngine:
    """Tests for OCR engine selection."""

    def test_get_ocr_engine_returns_callable(self):
        """OCR engine should be callable."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("video_no_ui", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        
        engine = mod._get_ocr_engine()
        assert callable(engine)


class TestVideoNoUICLI:
    """CLI-level tests for prd_test_video_no_ui.py."""

    def test_help(self):
        """Script should show help."""
        result = _run(["--help"])
        assert result.returncode == 0
        assert "usage:" in result.stdout.lower()

    def test_no_args_shows_error(self):
        """Running without arguments should show error."""
        result = _run([])
        assert result.returncode != 0

    def test_nonexistent_file_fails(self):
        """Non-existent video file should fail."""
        result = _run(["/nonexistent/video.mp4"])
        assert result.returncode != 0

    def test_clean_video_passes(self):
        """A clean video (no UI) should pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = _create_test_video(Path(tmpdir))
            
            if video_path.exists() and video_path.stat().st_size > 100:
                # Only run if we actually created a video
                result = _run([str(video_path), "--frames", "3"])
                # Should pass (exit 0) for clean video
                assert result.returncode == 0, f"Expected pass but got: {result.stderr}"
            else:
                pytest.skip("ffmpeg not available to create test video")

    def test_verbose_flag(self):
        """Verbose flag should be accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = _create_test_video(Path(tmpdir))
            
            if video_path.exists() and video_path.stat().st_size > 100:
                result = _run(["--verbose", str(video_path), "--frames", "3"])
                # Should complete without error about unknown flag
                assert "--verbose" not in result.stderr
            else:
                pytest.skip("ffmpeg not available to create test video")

    def test_custom_frame_count(self):
        """Custom frame count should be accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = _create_test_video(Path(tmpdir))
            
            if video_path.exists() and video_path.stat().st_size > 100:
                result = _run([str(video_path), "--frames", "5"])
                # Should handle custom frame count
                assert "unrecognized" not in result.stderr.lower()
            else:
                pytest.skip("ffmpeg not available to create test video")
