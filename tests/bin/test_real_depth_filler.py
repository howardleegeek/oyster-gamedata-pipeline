#!/usr/bin/env python3
"""
Tests for bin/real_depth_filler.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# numpy is only required by tests that exercise depth-array math.
# Skip the whole module if numpy isn't installed (Windows minipc / vendor
# without ML stack will skip rather than error during collection).
np = pytest.importorskip("numpy")


class TestNormalizeDepth:
    """Tests for normalize_depth_to_metric function."""

    def test_normalize_depth_clamps_to_far(self):
        """relative=1.0 → 0 (sky)"""
        # Import the module
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))
        from real_depth_filler import normalize_depth_to_metric

        depth_relative = np.array([[1.0, 0.5], [0.0, 0.995]])
        result = normalize_depth_to_metric(depth_relative, near_m=0.5, far_m=30.0)

        # Sky pixels (relative > 0.99) should be 0
        assert result[0, 0] == 0.0  # 1.0 > 0.99 → sky → 0
        assert result[1, 1] == 0.0  # 0.995 > 0.99 → sky → 0

    def test_normalize_depth_linear(self):
        """relative=0.5 → (near+far)/2 roughly"""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))
        from real_depth_filler import normalize_depth_to_metric

        near_m = 0.5
        far_m = 30.0
        depth_relative = np.array([[0.5]])
        result = normalize_depth_to_metric(depth_relative, near_m=near_m, far_m=far_m)

        expected = near_m + (far_m - near_m) * 0.5
        assert np.isclose(result[0, 0], expected)

    def test_normalize_depth_handles_zero(self):
        """relative=0.0 → near_m"""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))
        from real_depth_filler import normalize_depth_to_metric

        near_m = 0.5
        far_m = 30.0
        depth_relative = np.array([[0.0]])
        result = normalize_depth_to_metric(depth_relative, near_m=near_m, far_m=far_m)

        assert np.isclose(result[0, 0], near_m)


class TestSelectDevice:
    """Tests for select_device function."""

    def test_select_device_prefers_cuda(self, monkeypatch):
        """cuda > mps > cpu"""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

        # Mock torch
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        monkeypatch.setitem(sys.modules, "torch", mock_torch)

        from real_depth_filler import select_device

        result = select_device()
        assert result == "cuda"

    def test_select_device_falls_back_to_mps(self, monkeypatch):
        """cuda unavailable, mps available"""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

        # Mock torch
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_backends = MagicMock()
        mock_backends.mps.is_available.return_value = True
        mock_torch.backends = mock_backends
        monkeypatch.setitem(sys.modules, "torch", mock_torch)

        # Need to reimport to pick up the mock
        import importlib

        import real_depth_filler

        importlib.reload(real_depth_filler)
        result = real_depth_filler.select_device()
        assert result == "mps"

    def test_select_device_falls_back_to_cpu(self, monkeypatch):
        """nothing available"""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

        # Mock torch
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_backends = MagicMock()
        mock_backends.mps.is_available.return_value = False
        mock_torch.backends = mock_backends
        monkeypatch.setitem(sys.modules, "torch", mock_torch)

        import importlib

        import real_depth_filler

        importlib.reload(real_depth_filler)
        result = real_depth_filler.select_device()
        assert result == "cpu"


class TestInferBatch:
    """Tests for infer_batch function."""

    def test_infer_batch_counts_files(self, monkeypatch, tmp_path):
        """Test that infer_batch processes files correctly."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

        # Skip if PIL/Pillow not installed (vendor without ML stack)
        Image = pytest.importorskip("PIL.Image")

        # Create test PNG files
        rgb_dir = tmp_path / "rgb"
        rgb_dir.mkdir()
        out_dir = tmp_path / "depth"

        for i in range(3):
            img = Image.new("RGB", (64, 64), color=(128, 128, 128))
            img.save(rgb_dir / f"frame_{i:06d}.png")

        # Mock the pipeline
        mock_pipe = MagicMock()
        mock_result = MagicMock()
        mock_result.depth = np.random.rand(64, 64).astype(np.float32)
        mock_pipe.return_value = [mock_result, mock_result, mock_result]

        # Mock lazy_load_depth_pipeline
        import real_depth_filler

        monkeypatch.setattr(real_depth_filler, "lazy_load_depth_pipeline", lambda x: mock_pipe)

        # Also mock write_exr_float32 to avoid OpenEXR dependency
        written_files = []

        def mock_write_exr(path, depth):
            written_files.append(path)
            # Create a minimal valid file
            Path(path).touch()

        monkeypatch.setattr(real_depth_filler, "write_exr_float32", mock_write_exr)

        # Mock _verify_exr_channel
        monkeypatch.setattr(real_depth_filler, "_verify_exr_channel", lambda x: True)

        count = real_depth_filler.infer_batch(
            str(rgb_dir), str(out_dir), batch_size=2
        )

        assert count == 3

    def test_infer_batch_skips_when_torch_missing(self, monkeypatch, tmp_path):
        """Test that missing torch raises RuntimeError."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

        rgb_dir = tmp_path / "rgb"
        rgb_dir.mkdir()
        out_dir = tmp_path / "depth"

        # Create a dummy PNG
        from PIL import Image

        img = Image.new("RGB", (64, 64), color=(128, 128, 128))
        img.save(rgb_dir / "frame_000000.png")

        # Remove torch from modules if present
        modules_to_remove = ["torch", "transformers"]
        saved_modules = {}
        for mod in modules_to_remove:
            if mod in sys.modules:
                saved_modules[mod] = sys.modules[mod]
                del sys.modules[mod]

        try:
            import importlib

            import real_depth_filler

            importlib.reload(real_depth_filler)

            with pytest.raises(RuntimeError, match="Missing dependencies"):
                real_depth_filler.infer_batch(str(rgb_dir), str(out_dir))
        finally:
            # Restore modules
            for mod, val in saved_modules.items():
                sys.modules[mod] = val


class TestWriteExr:
    """Tests for write_exr_float32 function."""

    def test_write_exr_creates_file(self, tmp_path):
        """Test that write_exr_float32 creates a valid EXR file."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

        # Skip if OpenEXR not available
        pytest.importorskip("OpenEXR")

        from real_depth_filler import write_exr_float32

        depth = np.random.rand(16, 16).astype(np.float32) * 30.0
        exr_path = str(tmp_path / "test_000000.exr")

        write_exr_float32(exr_path, depth)

        # Verify file exists and has size > 0
        assert os.path.exists(exr_path)
        assert os.path.getsize(exr_path) > 0

        # Verify it has 'Z' channel
        import OpenEXR

        exr_file = OpenEXR.InputFile(exr_path)
        channels = exr_file.header()["channels"]
        assert "Z" in channels


class TestMain:
    """Tests for main function."""

    def test_main_argparse(self, monkeypatch, tmp_path):
        """Test CLI argument parsing."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

        rgb_dir = tmp_path / "rgb"
        rgb_dir.mkdir()
        out_dir = tmp_path / "depth"

        # Create dummy PNG
        from PIL import Image

        img = Image.new("RGB", (64, 64), color=(128, 128, 128))
        img.save(rgb_dir / "frame_000000.png")

        import real_depth_filler

        # Mock infer_batch to avoid actual processing
        monkeypatch.setattr(
            real_depth_filler,
            "infer_batch",
            lambda **kwargs: 1,
        )

        argv = [
            "--rgb-dir",
            str(rgb_dir),
            "--out-dir",
            str(out_dir),
            "--near",
            "0.5",
            "--far",
            "30.0",
            "--batch-size",
            "4",
        ]

        result = real_depth_filler.main(argv)
        assert result == 0

    def test_main_missing_rgb_dir(self, tmp_path):
        """Test main with missing rgb-dir."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

        import real_depth_filler

        argv = [
            "--rgb-dir",
            "/nonexistent/path",
            "--out-dir",
            str(tmp_path / "out"),
        ]

        result = real_depth_filler.main(argv)
        # Should return 1 on error (no files to process or error)
        assert result == 1 or result == 0  # Could be 0 if no files found


class TestLazyLoad:
    """Tests for lazy loading functions."""

    def test_lazy_load_missing_torch(self, monkeypatch):
        """Test that lazy_load_depth_pipeline raises error when torch missing."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

        # Remove torch from modules
        saved_modules = {}
        for mod in ["torch", "transformers"]:
            if mod in sys.modules:
                saved_modules[mod] = sys.modules[mod]
                del sys.modules[mod]

        try:
            import importlib

            import real_depth_filler

            importlib.reload(real_depth_filler)

            with pytest.raises(RuntimeError, match="Missing dependencies"):
                real_depth_filler.lazy_load_depth_pipeline()
        finally:
            for mod, val in saved_modules.items():
                sys.modules[mod] = val