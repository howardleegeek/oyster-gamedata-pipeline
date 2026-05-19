"""Tests for depth inference pipeline."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestExtractFrames:
    """Test cases for extract_frames function."""

    def test_extract_frames_calls_ffmpeg(self, monkeypatch, tmp_path):
        """Test that extract_frames calls ffmpeg with correct arguments."""
        from depth_inference_pipeline import extract_frames

        video_path = str(tmp_path / "test_video.mp4")
        output_dir = str(tmp_path / "frames")

        # Create a dummy video file
        Path(video_path).touch()

        # Track subprocess.run calls
        run_calls = []

        def mock_run(cmd, capture_output=False, text=False, check=False):
            run_calls.append(cmd)
            # Create fake frame files (using 6-digit format matching frame_%06d.png)
            os.makedirs(output_dir, exist_ok=True)
            for i in range(3):
                Path(os.path.join(output_dir, f"frame_{i+1:06d}.png")).touch()
            return MagicMock(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(subprocess, "run", mock_run)

        frames = extract_frames(video_path, output_dir, fps=30)

        # Verify ffmpeg was called correctly
        assert len(run_calls) == 1
        cmd = run_calls[0]
        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert video_path in cmd
        assert any("fps=30" in arg for arg in cmd)

        # Verify frames were returned
        assert len(frames) == 3
        assert all(f.endswith(".png") for f in frames)

    def test_extract_frames_handles_ffmpeg_error(self, monkeypatch, tmp_path):
        """Test that extract_frames raises error on ffmpeg failure."""
        from depth_inference_pipeline import extract_frames

        video_path = str(tmp_path / "test_video.mp4")
        output_dir = str(tmp_path / "frames")

        Path(video_path).touch()

        def mock_run(cmd, capture_output=False, text=False, check=False):
            # CalledProcessError signature: returncode, cmd, output=None, stderr=None
            raise subprocess.CalledProcessError(1, cmd, stderr="ffmpeg error: invalid codec")

        monkeypatch.setattr(subprocess, "run", mock_run)

        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            extract_frames(video_path, output_dir)


class TestInferDepthBatch:
    """Test cases for infer_depth_batch function."""

    def test_infer_depth_batch_raises_when_torch_missing(self, monkeypatch, tmp_path):
        """Test that infer_depth_batch raises error when torch is not available."""
        from depth_inference_pipeline import infer_depth_batch

        frame_path = str(tmp_path / "frame_000001.png")
        output_dir = str(tmp_path / "depth")

        # Create a dummy frame file
        Path(frame_path).touch()

        # Remove torch from sys.modules to simulate it being missing
        original_torch = sys.modules.get("torch")
        original_numpy = sys.modules.get("numpy")
        original_pil = sys.modules.get("PIL")

        try:
            # Remove modules
            if "torch" in sys.modules:
                del sys.modules["torch"]
            if "numpy" in sys.modules:
                del sys.modules["numpy"]
            if "PIL" in sys.modules:
                del sys.modules["PIL"]

            with pytest.raises(RuntimeError, match="torch"):
                infer_depth_batch([frame_path], output_dir)

        finally:
            # Restore modules
            if original_torch is not None:
                sys.modules["torch"] = original_torch
            if original_numpy is not None:
                sys.modules["numpy"] = original_numpy
            if original_pil is not None:
                sys.modules["PIL"] = original_pil