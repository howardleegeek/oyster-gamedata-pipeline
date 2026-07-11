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

        def mock_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            run_calls.append(cmd)
            # Create fake frame files
            os.makedirs(output_dir, exist_ok=True)
            for i in range(3):
                Path(os.path.join(output_dir, f"frame_{i+1:04d}.png")).touch()
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
        from depth_inference_pipeline import DepthInferenceError, extract_frames

        video_path = str(tmp_path / "test_video.mp4")
        output_dir = str(tmp_path / "frames")

        Path(video_path).touch()

        def mock_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            if check:
                raise subprocess.CalledProcessError(1, cmd, "ffmpeg error", "")
            return MagicMock(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(subprocess, "run", mock_run)

        with pytest.raises(DepthInferenceError, match="ffmpeg failed"):
            extract_frames(video_path, output_dir)


class TestInferDepth:
    """Test cases for infer_depth function."""

    def test_infer_depth_skips_when_torch_missing(self, monkeypatch, tmp_path):
        """Test that infer_depth raises error when torch is not available."""
        from depth_inference_pipeline import DepthInferenceError, infer_depth

        frame_path = str(tmp_path / "frame_0001.png")
        output_path = str(tmp_path / "depth_0001.png")

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

            # Block imports
            import builtins

            real_import = builtins.__import__

            def block_import(name, *args, **kwargs):
                if name in ("torch", "numpy", "PIL"):
                    raise ImportError(f"No module named '{name}'")
                return real_import(name, *args, **kwargs)

            monkeypatch.setattr(builtins, "__import__", block_import)

            with pytest.raises(DepthInferenceError, match="Missing dependency"):
                infer_depth(frame_path, output_path)

        finally:
            # Restore modules
            if original_torch is not None:
                sys.modules["torch"] = original_torch
            if original_numpy is not None:
                sys.modules["numpy"] = original_numpy
            if original_pil is not None:
                sys.modules["PIL"] = original_pil


class TestVideoToDepth:
    """Test cases for video_to_depth function."""

    def test_video_to_depth_chains(self, monkeypatch, tmp_path):
        """Test that video_to_depth chains extract_frames and infer_depth."""
        from depth_inference_pipeline import video_to_depth

        video_path = str(tmp_path / "test_video.mp4")
        output_dir = str(tmp_path / "depth_maps")

        Path(video_path).touch()

        # Track function calls
        extract_called = []
        infer_called = []

        def mock_extract_frames(video, out_dir, fps=30):
            extract_called.append((video, out_dir, fps))
            # Create fake frames
            os.makedirs(out_dir, exist_ok=True)
            frames = []
            for i in range(3):
                frame_path = os.path.join(out_dir, f"frame_{i:04d}.png")
                Path(frame_path).touch()
                frames.append(frame_path)
            return frames

        def mock_infer_depth(frame_path, out_path):
            infer_called.append((frame_path, out_path))
            Path(out_path).touch()
            return out_path

        monkeypatch.setattr("depth_inference_pipeline.extract_frames", mock_extract_frames)
        monkeypatch.setattr("depth_inference_pipeline.infer_depth", mock_infer_depth)

        result = video_to_depth(video_path, output_dir, cleanup=False)

        # Verify extract_frames was called
        assert len(extract_called) == 1
        assert extract_called[0][0] == video_path

        # Verify infer_depth was called for each frame
        assert len(infer_called) == 3

        # Verify result contains depth maps
        assert len(result) == 3

    def test_video_to_depth_cleans_up_temp(self, monkeypatch, tmp_path):
        """Test that video_to_depth cleans up temporary files."""
        from depth_inference_pipeline import video_to_depth

        video_path = str(tmp_path / "test_video.mp4")
        output_dir = str(tmp_path / "depth_maps")

        Path(video_path).touch()

        temp_dirs_created = []

        def mock_extract_frames(video, out_dir, fps=30):
            temp_dirs_created.append(out_dir)
            os.makedirs(out_dir, exist_ok=True)
            # Create some temp files
            for i in range(2):
                Path(os.path.join(out_dir, f"frame_{i:04d}.png")).touch()
            return [os.path.join(out_dir, f"frame_{i:04d}.png") for i in range(2)]

        def mock_infer_depth(frame_path, out_path):
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            Path(out_path).touch()
            return out_path

        # Track mkdtemp and rmtree
        original_mkdtemp = __import__("tempfile").mkdtemp
        original_rmtree = __import__("shutil").rmtree

        mkdtemp_calls = []
        rmtree_calls = []

        def mock_mkdtemp(prefix=None):
            temp_dir = original_mkdtemp(prefix=prefix)
            mkdtemp_calls.append(temp_dir)
            return temp_dir

        def mock_rmtree(path):
            rmtree_calls.append(path)
            original_rmtree(path)

        monkeypatch.setattr("tempfile.mkdtemp", mock_mkdtemp)
        monkeypatch.setattr("shutil.rmtree", mock_rmtree)
        monkeypatch.setattr("depth_inference_pipeline.extract_frames", mock_extract_frames)
        monkeypatch.setattr("depth_inference_pipeline.infer_depth", mock_infer_depth)

        video_to_depth(video_path, output_dir, cleanup=True)

        # Verify temp directory was created
        assert len(mkdtemp_calls) == 1

        # Verify cleanup was called
        assert len(rmtree_calls) == 1
        assert rmtree_calls[0] == mkdtemp_calls[0]

        # Verify temp dir no longer exists
        assert not os.path.exists(mkdtemp_calls[0])
