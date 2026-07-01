#!/usr/bin/env python3
"""Tests for bin/autoresearch_depth_quality.py"""

from __future__ import annotations

import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest


class TestComputeMetrics:
    """Tests for _compute_metrics function."""

    def test_perfect_prediction(self):
        """Test that identical arrays give zero error."""
        from bin.autoresearch_depth_quality import _compute_metrics

        gt = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        pred = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _compute_metrics(gt, pred)

        assert result["abs_rel"] == 0.0
        assert result["rmse"] == 0.0
        assert result["delta_1"] == 1.0

    def test_empty_mask_returns_nan(self):
        """Test that all-zero ground truth returns NaN metrics."""
        import math

        from bin.autoresearch_depth_quality import _compute_metrics

        gt = np.array([0.0, 0.0, 0.0])
        pred = np.array([1.0, 2.0, 3.0])
        result = _compute_metrics(gt, pred)

        assert math.isnan(result["abs_rel"])
        assert math.isnan(result["rmse"])
        assert math.isnan(result["delta_1"])

    def test_returns_dict_with_all_keys(self):
        """Test that result has all required keys."""
        from bin.autoresearch_depth_quality import _compute_metrics

        gt = np.array([1.0, 2.0, 3.0])
        pred = np.array([1.1, 2.1, 3.1])
        result = _compute_metrics(gt, pred)

        assert "abs_rel" in result
        assert "rmse" in result
        assert "delta_1" in result


class TestLoadImage:
    """Tests for _load_image function."""

    def test_loads_grayscale_image(self):
        """Test that image is loaded and converted to grayscale float64."""
        from PIL import Image

        from bin.autoresearch_depth_quality import _load_image

        # Create a temp image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img_path = Path(f.name)

        try:
            # Create a simple test image using PIL directly
            img = Image.new("RGB", (10, 10), color="red")
            img.save(img_path)

            result = _load_image(img_path)

            # Should be grayscale
            assert result is not None
            assert result.dtype == np.float64
        finally:
            img_path.unlink(missing_ok=True)


class TestLoadZbuffer:
    """Tests for _load_zbuffer function."""

    def test_loads_npy_file(self):
        """Test loading .npy files."""
        from bin.autoresearch_depth_quality import _load_zbuffer

        with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as f:
            npy_path = Path(f.name)

        try:
            # Write a test numpy array
            test_arr = np.array([[1, 2], [3, 4]], dtype=np.float64)
            np.save(npy_path, test_arr)

            result = _load_zbuffer(npy_path)

            assert result is not None
            assert result.shape == (2, 2)
        finally:
            npy_path.unlink(missing_ok=True)

    def test_loads_image_file(self):
        """Test loading image files (calls _load_image)."""
        from PIL import Image

        from bin.autoresearch_depth_quality import _load_zbuffer

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img_path = Path(f.name)

        try:
            img = Image.new("L", (10, 10), color=128)
            img.save(img_path)

            result = _load_zbuffer(img_path)

            assert result is not None
        finally:
            img_path.unlink(missing_ok=True)


class TestCollectFrames:
    """Tests for _collect_frames function."""

    def test_pairs_matching_stems(self):
        """Test that matching files are paired correctly."""
        from bin.autoresearch_depth_quality import _collect_frames

        with tempfile.TemporaryDirectory() as tmpdir:
            gt_dir = Path(tmpdir) / "gt"
            pred_dir = Path(tmpdir) / "pred"
            gt_dir.mkdir()
            pred_dir.mkdir()

            # Create matching files
            (gt_dir / "frame001.png").write_text("dummy")
            (gt_dir / "frame002.png").write_text("dummy")
            (pred_dir / "frame001.png").write_text("dummy")
            (pred_dir / "frame002.png").write_text("dummy")

            result = _collect_frames(gt_dir, pred_dir)

            assert len(result) == 2
            stems = {r[0].stem for r in result}
            assert stems == {"frame001", "frame002"}

    def test_no_common_files(self):
        """Test with no matching files returns empty list."""
        from bin.autoresearch_depth_quality import _collect_frames

        with tempfile.TemporaryDirectory() as tmpdir:
            gt_dir = Path(tmpdir) / "gt"
            pred_dir = Path(tmpdir) / "pred"
            gt_dir.mkdir()
            pred_dir.mkdir()

            (gt_dir / "frame001.png").write_text("dummy")
            (pred_dir / "frame002.png").write_text("dummy")

            result = _collect_frames(gt_dir, pred_dir)

            assert len(result) == 0


class TestBuildParser:
    """Tests for build_parser function."""

    def test_parser_has_required_arguments(self):
        """Test that parser has all required arguments."""
        from bin.autoresearch_depth_quality import build_parser

        parser = build_parser()

        # Parse with required args should not raise
        args = parser.parse_args([
            "--gt-dir", "/path/to/gt",
            "--da-dir", "/path/to/da",
            "--mg-dir", "/path/to/mg"
        ])

        assert args.gt_dir == Path("/path/to/gt")
        assert args.da_dir == Path("/path/to/da")
        assert args.mg_dir == Path("/path/to/mg")
        assert args.max_frames == 50  # default

    def test_parser_optional_arguments(self):
        """Test optional arguments."""
        from bin.autoresearch_depth_quality import build_parser

        parser = build_parser()

        args = parser.parse_args([
            "--gt-dir", "/path/to/gt",
            "--da-dir", "/path/to/da",
            "--mg-dir", "/path/to/mg",
            "--max-frames", "100",
            "--output", "report.xlsx",
            "-v"
        ])

        assert args.max_frames == 100
        assert args.output == Path("report.xlsx")
        assert args.verbose is True


class TestMain:
    """Tests for main function."""

    def test_main_shows_help(self):
        """Test that main shows help and exits."""
        from bin.autoresearch_depth_quality import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_main_missing_required_args(self):
        """Test that main fails with missing required args."""
        from bin.autoresearch_depth_quality import main

        with pytest.raises(SystemExit):
            main([])  # No args


class TestPrintReport:
    """Tests for _print_report function."""

    def test_print_report_format(self):
        """Test that report prints in expected format."""
        from bin.autoresearch_depth_quality import _print_report

        results = {
            "models": {
                "DepthAnything": {
                    "n_frames": 10,
                    "aggregate": {"abs_rel": 0.15, "rmse": 0.5, "delta_1": 0.85}
                },
                "Marigold": {
                    "n_frames": 10,
                    "aggregate": {"abs_rel": 0.12, "rmse": 0.4, "delta_1": 0.9}
                }
            }
        }

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            _print_report(results)
            output = sys.stdout.getvalue()
            assert "DepthAnything" in output
            assert "Marigold" in output
            assert "AbsRel" in output
            assert "RMSE" in output
        finally:
            sys.stdout = old_stdout


class TestWriteExcel:
    """Tests for _write_excel function."""

    def test_write_excel_creates_file(self):
        """Test that Excel file is created with correct structure."""
        from unittest.mock import patch

        from bin.autoresearch_depth_quality import _write_excel

        results = {
            "models": {
                "DepthAnything": {
                    "n_frames": 10,
                    "aggregate": {"abs_rel": 0.15, "rmse": 0.5, "delta_1": 0.85},
                    "per_frame": [
                        {"abs_rel": 0.1, "rmse": 0.4, "delta_1": 0.9},
                        {"abs_rel": 0.2, "rmse": 0.6, "delta_1": 0.8}
                    ]
                }
            }
        }

        # Mock openpyxl
        mock_wb = MagicMock()
        mock_ws = MagicMock()
        mock_ws2 = MagicMock()
        mock_wb.active = mock_ws
        mock_wb.create_sheet = MagicMock(return_value=mock_ws2)

        mock_openpyxl = MagicMock()
        mock_openpyxl.Workbook = MagicMock(return_value=mock_wb)

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output_path = Path(f.name)

        try:
            with patch("bin.autoresearch_depth_quality._import_openpyxl", return_value=mock_openpyxl):
                _write_excel(results, output_path)

                mock_openpyxl.Workbook.assert_called_once()
                mock_wb.save.assert_called_once()
        finally:
            if output_path.exists():
                output_path.unlink()


class TestRunComparison:
    """Tests for run_comparison function."""

    def test_run_comparison_basic(self):
        """Test basic comparison run."""
        from unittest.mock import patch

        from bin.autoresearch_depth_quality import run_comparison

        with tempfile.TemporaryDirectory() as tmpdir:
            gt_dir = Path(tmpdir) / "gt"
            da_dir = Path(tmpdir) / "da"
            mg_dir = Path(tmpdir) / "mg"

            gt_dir.mkdir()
            da_dir.mkdir()
            mg_dir.mkdir()

            # Create dummy files
            (gt_dir / "frame001.npy").write_text("dummy")
            (da_dir / "frame001.npy").write_text("dummy")
            (mg_dir / "frame001.npy").write_text("dummy")

            # Mock _load_zbuffer to return proper arrays
            test_arr = np.array([[1.0, 2.0], [3.0, 4.0]])
            with patch("bin.autoresearch_depth_quality._load_zbuffer", return_value=test_arr):
                result = run_comparison(
                    gt_dir,
                    {"DepthAnything": da_dir, "Marigold": mg_dir},
                    max_frames=10
                )

                assert "models" in result
                assert "summary" in result
                assert "DepthAnything" in result["models"]
                assert "Marigold" in result["models"]

    def test_run_comparison_empty_dirs(self):
        """Test comparison with no matching files."""
        from bin.autoresearch_depth_quality import run_comparison

        with tempfile.TemporaryDirectory() as tmpdir:
            gt_dir = Path(tmpdir) / "gt"
            da_dir = Path(tmpdir) / "da"
            mg_dir = Path(tmpdir) / "mg"

            gt_dir.mkdir()
            da_dir.mkdir()
            mg_dir.mkdir()

            # No matching files
            result = run_comparison(
                gt_dir,
                {"DepthAnything": da_dir},
                max_frames=10
            )

            # With no matching frames, models dict is empty (skips models with no frames)
            assert "models" in result
            assert result["models"] == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
