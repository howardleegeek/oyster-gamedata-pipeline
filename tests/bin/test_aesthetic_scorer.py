#!/usr/bin/env python3
"""Tests for bin/aesthetic_scorer.py — G159 Aesthetic Scorer."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Import the module under test
import bin.aesthetic_scorer as aesthetic_scorer


class TestLazyImports:
    """Test _lazy_imports function."""

    def test_lazy_imports_returns_numpy_and_pil(self):
        """_lazy_imports should return numpy and PIL.Image modules."""
        np, Image = aesthetic_scorer._lazy_imports()
        assert np is not None
        assert Image is not None

    def test_lazy_imports_cached(self):
        """_lazy_imports should return same objects on subsequent calls."""
        np1, Image1 = aesthetic_scorer._lazy_imports()
        np2, Image2 = aesthetic_scorer._lazy_imports()
        assert np1 is np2
        assert Image1 is Image2


class TestToGray:
    """Test _to_gray helper function."""

    def test_to_gray_rgb_to_gray(self):
        """_to_gray should convert RGB array to grayscale."""
        np = aesthetic_scorer._lazy_imports()[0]
        # Create a simple RGB frame (height=2, width=2, channels=3)
        rgb_frame = np.array([[[255, 0, 0], [0, 255, 0]], [[0, 0, 255], [128, 128, 128]]])
        gray = aesthetic_scorer._to_gray(rgb_frame)
        assert gray.shape == (2, 2)
        # Grayscale conversion returns float array
        assert gray.dtype == np.float64

    def test_to_gray_single_frame(self):
        """_to_gray should handle single row of pixels."""
        np = aesthetic_scorer._lazy_imports()[0]
        rgb_frame = np.array([[[100, 100, 100]]])
        gray = aesthetic_scorer._to_gray(rgb_frame)
        assert gray.shape == (1, 1)


class TestComputeAestheticScore:
    """Test compute_aesthetic_score function."""

    def test_aesthetic_score_zero_frames(self):
        """Should return 0.0 for empty frame list."""
        result = aesthetic_scorer.compute_aesthetic_score([])
        assert result == 0.0

    def test_aesthetic_score_single_frame(self):
        """Should return score for single frame."""
        np = aesthetic_scorer._lazy_imports()[0]
        # Create a simple test frame (480x320 RGB)
        frame = np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8)
        result = aesthetic_scorer.compute_aesthetic_score([frame])
        assert 0.0 <= result <= 1.0

    def test_aesthetic_score_multiple_frames(self):
        """Should return score for multiple frames."""
        np = aesthetic_scorer._lazy_imports()[0]
        frames = [
            np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8)
            for _ in range(5)
        ]
        result = aesthetic_scorer.compute_aesthetic_score(frames)
        assert 0.0 <= result <= 1.0


class TestComputeMotionScore:
    """Test compute_motion_score function."""

    def test_motion_score_zero_frames(self):
        """Should return 0.0 for empty frame list."""
        result = aesthetic_scorer.compute_motion_score([])
        assert result == 0.0

    def test_motion_score_single_frame(self):
        """Should return 0.0 for single frame (no motion possible)."""
        np = aesthetic_scorer._lazy_imports()[0]
        frame = np.zeros((320, 480, 3), dtype=np.uint8)
        result = aesthetic_scorer.compute_motion_score([frame])
        assert result == 0.0

    def test_motion_score_static_frames(self):
        """Should return low score for identical frames."""
        np = aesthetic_scorer._lazy_imports()[0]
        frame = np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8)
        frames = [frame, frame, frame]
        result = aesthetic_scorer.compute_motion_score(frames)
        assert result < 0.1

    def test_motion_score_different_frames(self):
        """Should return higher score for different frames."""
        np = aesthetic_scorer._lazy_imports()[0]
        frames = [
            np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8)
            for _ in range(3)
        ]
        result = aesthetic_scorer.compute_motion_score(frames)
        assert 0.0 <= result <= 1.0


class TestDetectOcrOverlay:
    """Test detect_ocr_overlay function."""

    def test_ocr_zero_frames(self):
        """Should return default result for empty frame list."""
        result = aesthetic_scorer.detect_ocr_overlay([])
        assert result["has_ocr"] is False
        assert result["confidence"] == 0.0
        assert result["affected_frames"] == []

    def test_ocr_single_frame(self):
        """Should analyze single frame."""
        np = aesthetic_scorer._lazy_imports()[0]
        frame = np.zeros((320, 480, 3), dtype=np.uint8)
        result = aesthetic_scorer.detect_ocr_overlay([frame])
        assert "has_ocr" in result
        assert "confidence" in result
        assert "affected_frames" in result

    def test_ocr_with_threshold(self):
        """Should accept custom threshold parameter."""
        np = aesthetic_scorer._lazy_imports()[0]
        frame = np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8)
        result = aesthetic_scorer.detect_ocr_overlay([frame], threshold=0.5)
        assert "confidence" in result


class TestComputeCameraJitter:
    """Test compute_camera_jitter function."""

    def test_jitter_zero_frames(self):
        """Should return 0.0 for empty frame list."""
        result = aesthetic_scorer.compute_camera_jitter([])
        assert result == 0.0

    def test_jitter_single_frame(self):
        """Should return 0.0 for single frame."""
        np = aesthetic_scorer._lazy_imports()[0]
        frame = np.zeros((320, 480, 3), dtype=np.uint8)
        result = aesthetic_scorer.compute_camera_jitter([frame])
        assert result == 0.0

    def test_jitter_static_frames(self):
        """Should return low jitter for identical frames."""
        np = aesthetic_scorer._lazy_imports()[0]
        frame = np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8)
        frames = [frame, frame, frame]
        result = aesthetic_scorer.compute_camera_jitter(frames)
        assert result < 0.1

    def test_jitter_varying_frames(self):
        """Should return score for varying frames."""
        np = aesthetic_scorer._lazy_imports()[0]
        frames = [
            np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8)
            for _ in range(3)
        ]
        result = aesthetic_scorer.compute_camera_jitter(frames)
        assert 0.0 <= result <= 1.0


class TestScoreClip:
    """Test score_clip function."""

    def test_score_clip_structure(self):
        """score_clip should return dict with expected keys."""
        np = aesthetic_scorer._lazy_imports()[0]
        frames = [np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8) for _ in range(3)]
        result = aesthetic_scorer.score_clip(frames)
        assert "aesthetic" in result
        assert "motion" in result
        assert "ocr" in result
        assert "jitter" in result
        assert "composite" in result

    def test_score_clip_ocr_structure(self):
        """score_clip OCR key should have expected sub-keys."""
        np = aesthetic_scorer._lazy_imports()[0]
        frames = [np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8) for _ in range(3)]
        result = aesthetic_scorer.score_clip(frames)
        assert "has_ocr" in result["ocr"]
        assert "confidence" in result["ocr"]
        assert "affected_frames" in result["ocr"]

    def test_score_clip_value_ranges(self):
        """All scores should be in valid range [0.0, 1.0]."""
        np = aesthetic_scorer._lazy_imports()[0]
        frames = [np.random.randint(0, 256, (320, 480, 3), dtype=np.uint8) for _ in range(3)]
        result = aesthetic_scorer.score_clip(frames)
        assert 0.0 <= result["aesthetic"] <= 1.0
        assert 0.0 <= result["motion"] <= 1.0
        assert 0.0 <= result["ocr"]["confidence"] <= 1.0
        assert 0.0 <= result["jitter"] <= 1.0
        assert 0.0 <= result["composite"] <= 1.0


class TestProcessSingle:
    """Test process_single function."""

    def test_process_single_unsupported_file(self):
        """Should raise ValueError for unsupported file extension."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("not a video")
            with pytest.raises(ValueError, match="Unsupported input"):
                aesthetic_scorer.process_single(str(test_file))

    def test_process_single_video_file_not_found(self):
        """Should raise error for non-existent video file."""
        with pytest.raises(Exception):
            aesthetic_scorer.process_single("/nonexistent/video.mp4")


class TestProcessBatch:
    """Test process_batch function."""

    def test_process_batch_empty_directory(self):
        """Should return empty list for empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = aesthetic_scorer.process_batch(tmpdir)
            assert result == []


class TestBuildParser:
    """Test build_parser function."""

    def test_parser_has_input_argument(self):
        """Parser should have --input / -i argument."""
        parser = aesthetic_scorer.build_parser()
        args = parser.parse_args(["--input", "test.mp4", "--output", "out.json"])
        assert args.input == "test.mp4"

    def test_parser_has_batch_argument(self):
        """Parser should have --batch / -b argument."""
        parser = aesthetic_scorer.build_parser()
        args = parser.parse_args(["--batch", "dir/", "--output", "out.csv"])
        assert args.batch == "dir/"

    def test_parser_has_output_argument(self):
        """Parser should have --output / -o required argument."""
        parser = aesthetic_scorer.build_parser()
        args = parser.parse_args(["--input", "test.mp4", "--output", "out.json"])
        assert args.output == "out.json"

    def test_parser_sample_frames_default(self):
        """Parser should have default sample-frames of 32."""
        parser = aesthetic_scorer.build_parser()
        args = parser.parse_args(["--input", "test.mp4", "--output", "out.json"])
        assert args.sample_frames == 32

    def test_parser_sample_frames_custom(self):
        """Parser should accept custom --sample-frames value."""
        parser = aesthetic_scorer.build_parser()
        args = parser.parse_args(
            ["--input", "test.mp4", "--output", "out.json", "--sample-frames", "16"]
        )
        assert args.sample_frames == 16

    def test_parser_ocr_threshold_default(self):
        """Parser should have default ocr-threshold of 0.3."""
        parser = aesthetic_scorer.build_parser()
        args = parser.parse_args(["--input", "test.mp4", "--output", "out.json"])
        assert args.ocr_threshold == 0.3

    def test_parser_verbose_flag(self):
        """Parser should accept -v / --verbose flag."""
        parser = aesthetic_scorer.build_parser()
        args = parser.parse_args(["--input", "test.mp4", "--output", "out.json", "-v"])
        assert args.verbose is True


class TestMain:
    """Test main function."""

    def test_main_missing_dependency(self, tmp_path):
        """main should return 1 when required dependencies missing."""
        # Test by patching lazy_imports to raise ImportError
        with mock.patch.object(
            aesthetic_scorer, "_lazy_imports", side_effect=ImportError("numpy")
        ):
            result = aesthetic_scorer.main(["--input", "test.mp4", "--output", "out.json"])
            assert result == 1

    def test_main_missing_output(self):
        """main should fail when output not specified (required)."""
        with pytest.raises(SystemExit):
            aesthetic_scorer.main(["--input", "test.mp4"])

    def test_main_no_input_or_batch(self):
        """main should require either --input or --batch."""
        # This should fail due to required --output and missing input/batch
        with pytest.raises(SystemExit):
            aesthetic_scorer.main(["--output", "out.json"])


class TestVideoExtensions:
    """Test _VIDEO_EXTS constant."""

    def test_video_extensions_set(self):
        """_VIDEO_EXTS should contain expected video extensions."""
        expected = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}
        assert aesthetic_scorer._VIDEO_EXTS == expected


class TestImageExtensions:
    """Test _IMAGE_EXTS constant."""

    def test_image_extensions_set(self):
        """_IMAGE_EXTS should contain expected image extensions."""
        expected = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}
        assert aesthetic_scorer._IMAGE_EXTS == expected
