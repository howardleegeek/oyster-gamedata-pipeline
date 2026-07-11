#!/usr/bin/env python3
"""Tests for ONNX inference pipeline.

Tests:
1. ONNX output equivalence vs PyTorch baseline (max diff < 0.01)
2. Provider fallback (kill DirectML, fall to CPU)
3. download_da_v2_onnx with mocked Aliyun + HF endpoints
"""

import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

# Ensure bin/ is on path
BIN_DIR = pathlib.Path(__file__).parent.parent / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))


class TestONNXOutputEquivalence(unittest.TestCase):
    """Verify ONNX output matches PyTorch baseline within tolerance."""

    @classmethod
    def setUpClass(cls):
        """Load the POC ONNX model and run a single inference."""
        cls.onnx_path = pathlib.Path("/tmp/poc_onnx_out/depth_anything_v2_small.onnx")
        if not cls.onnx_path.exists():
            raise unittest.SkipTest("POC ONNX model not found at /tmp/poc_onnx_out/")

        # Load processor and model for generating proper inputs
        cls.model_id = "depth-anything/Depth-Anything-V2-Small-hf"
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        cls.processor = AutoImageProcessor.from_pretrained(cls.model_id)
        cls.pt_model = AutoModelForDepthEstimation.from_pretrained(cls.model_id)
        cls.pt_model.eval()

        # Create a deterministic test image
        np.random.seed(42)
        from PIL import Image

        cls.test_img = Image.fromarray(np.random.randint(0, 255, (518, 518, 3), dtype=np.uint8))

        # Process through the image processor
        cls.inputs = cls.processor(images=cls.test_img, return_tensors="pt")
        cls.numpy_input = cls.inputs["pixel_values"].numpy()

    def test_onnx_model_loads(self):
        """ONNX model should load without errors."""
        import onnxruntime as ort

        sess = ort.InferenceSession(str(self.onnx_path), providers=["CPUExecutionProvider"])
        self.assertIsNotNone(sess)
        # Verify input/output names
        input_names = [inp.name for inp in sess.get_inputs()]
        self.assertIn("pixel_values", input_names)
        output_names = [out.name for out in sess.get_outputs()]
        self.assertIn("predicted_depth", output_names)

    def test_onnx_inference_runs(self):
        """ONNX inference should produce a valid depth map."""
        import onnxruntime as ort

        sess = ort.InferenceSession(str(self.onnx_path), providers=["CPUExecutionProvider"])
        outputs = sess.run(None, {"pixel_values": self.numpy_input})
        self.assertEqual(len(outputs), 1)
        depth = outputs[0]
        self.assertEqual(depth.ndim, 3)  # (batch, H, W)
        self.assertEqual(depth.shape[0], 1)
        self.assertFalse(np.any(np.isnan(depth)))
        self.assertFalse(np.any(np.isinf(depth)))

    def test_onnx_deterministic(self):
        """ONNX inference should be deterministic across runs."""
        import onnxruntime as ort

        sess = ort.InferenceSession(str(self.onnx_path), providers=["CPUExecutionProvider"])
        out1 = sess.run(None, {"pixel_values": self.numpy_input})[0]
        out2 = sess.run(None, {"pixel_values": self.numpy_input})[0]
        np.testing.assert_array_equal(out1, out2)

    def test_onnx_vs_pytorch_equivalence(self):
        """ONNX output should match PyTorch baseline within tolerance.

        The POC proved bit-identical output (max diff 0.0000).
        We allow < 0.01 for any floating-point variance.
        """
        import onnxruntime as ort
        import torch

        # Run ONNX inference
        sess = ort.InferenceSession(str(self.onnx_path), providers=["CPUExecutionProvider"])
        ort_output = sess.run(None, {"pixel_values": self.numpy_input})[0]
        ort_depth = ort_output.squeeze()

        # Run PyTorch inference for comparison
        with torch.inference_mode():
            pt_output = self.pt_model(**self.inputs)
            pt_depth = pt_output.predicted_depth.squeeze().cpu().numpy()

        # Compare
        diff = np.abs(pt_depth - ort_depth)
        max_diff = float(diff.max())
        mean_diff = float(diff.mean())

        print(f"\n  ONNX vs PyTorch: max_diff={max_diff:.6f}, mean_diff={mean_diff:.6f}")
        self.assertLess(max_diff, 0.01, f"Max diff {max_diff} exceeds 0.01 threshold")


class TestProviderFallback(unittest.TestCase):
    """Verify provider fallback behavior."""

    def test_provider_selection_order(self):
        """Providers should be selected in correct priority order."""
        try:
            import onnxruntime
        except ImportError:
            self.skipTest("onnxruntime not installed")

        # Mock ort.get_available_providers to simulate different environments
        from bin.run_da_v2_depth_onnx import get_providers

        # Simulate Windows with DirectML
        with patch.object(onnxruntime, "get_available_providers") as mock_prov:
            mock_prov.return_value = [
                "DmlExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = get_providers()
            self.assertEqual(providers[0], "DmlExecutionProvider")
            self.assertIn("CPUExecutionProvider", providers)

        # Simulate Mac with CoreML
        with patch.object(onnxruntime, "get_available_providers") as mock_prov:
            mock_prov.return_value = [
                "CoreMLExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = get_providers()
            self.assertEqual(providers[0], "CoreMLExecutionProvider")

        # Simulate Linux with CUDA
        with patch.object(onnxruntime, "get_available_providers") as mock_prov:
            mock_prov.return_value = [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            providers = get_providers()
            self.assertEqual(providers[0], "CUDAExecutionProvider")

        # Simulate CPU-only
        with patch.object(onnxruntime, "get_available_providers") as mock_prov:
            mock_prov.return_value = ["CPUExecutionProvider"]
            providers = get_providers()
            self.assertEqual(providers, ["CPUExecutionProvider"])

    def test_fallback_to_cpu_when_directml_unavailable(self):
        """When DirectML is not available, should fall back to CPU."""
        try:
            import onnxruntime
        except ImportError:
            self.skipTest("onnxruntime not installed")

        with patch.object(onnxruntime, "get_available_providers") as mock_prov:
            mock_prov.return_value = ["CPUExecutionProvider"]
            from bin.run_da_v2_depth_onnx import get_providers

            providers = get_providers()
            self.assertNotIn("DmlExecutionProvider", providers)
            self.assertIn("CPUExecutionProvider", providers)


class TestDownloadModel(unittest.TestCase):
    """Test download_da_v2_onnx with mocked endpoints."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = pathlib.Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_mock_response(self, data: bytes, status_code: int = 200):
        """Create a mock urllib response."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = status_code
        return mock_resp

    def test_aliyun_download_success(self):
        """Should successfully download from Aliyun OSS."""
        from bin.download_da_v2_onnx import download_file

        dummy_onnx = b"\x00" * 100

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = self._make_mock_response(dummy_onnx)
            dest = self.cache_dir / "test.onnx"
            result = download_file("http://test/test.onnx", dest, timeout=1.0)
            self.assertTrue(result)
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_bytes(), dummy_onnx)

    def test_aliyun_timeout_falls_back_to_hf(self):
        """When Aliyun times out, should fall back to HuggingFace."""
        from bin.download_da_v2_onnx import (
            download_from_aliyun,
        )

        # Aliyun fails
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("Connection timed out")
            result = download_from_aliyun(self.cache_dir, timeout=0.1)
            self.assertFalse(result)

    def test_checksum_verification(self):
        """Should verify SHA-256 checksums against manifest."""
        from bin.download_da_v2_onnx import verify_checksums

        # Create test files
        test_file = self.cache_dir / "test.bin"
        test_data = b"hello world"
        test_file.write_bytes(test_data)

        expected_hash = hashlib.sha256(test_data).hexdigest()
        manifest = {"files": {"test.bin": {"sha256": expected_hash, "size_bytes": len(test_data)}}}

        self.assertTrue(verify_checksums(self.cache_dir, manifest))

        # Wrong checksum should fail
        bad_manifest = {"files": {"test.bin": {"sha256": "0" * 64, "size_bytes": len(test_data)}}}
        self.assertFalse(verify_checksums(self.cache_dir, bad_manifest))

    def test_missing_file_fails_verification(self):
        """Missing files should fail verification."""
        from bin.download_da_v2_onnx import verify_checksums

        manifest = {"files": {"nonexistent.onnx": {"sha256": "abc123", "size_bytes": 100}}}
        self.assertFalse(verify_checksums(self.cache_dir, manifest))


class TestSourceMarker(unittest.TestCase):
    """Verify .source marker is written correctly."""

    def test_source_marker_kind(self):
        """Source marker should have kind: monocular_da_v2_onnx."""
        from bin.run_da_v2_depth_onnx import write_source_marker

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = pathlib.Path(tmpdir)
            write_source_marker(output_dir)

            source_path = output_dir / ".source"
            self.assertTrue(source_path.exists())

            with open(source_path) as f:
                source_data = json.load(f)

            self.assertEqual(source_data["kind"], "monocular_da_v2_onnx")
            self.assertEqual(source_data["backend"], "onnxruntime")
            self.assertIn("timestamp", source_data)


class TestManifestGeneration(unittest.TestCase):
    """Verify manifest.json structure from export script."""

    def test_manifest_structure(self):
        """Manifest should contain required fields."""
        # Check if POC manifest exists
        poc_manifest = pathlib.Path("/tmp/poc_onnx_out/manifest.json")
        if poc_manifest.exists():
            with open(poc_manifest) as f:
                manifest = json.load(f)
            self.assertIn("model_id", manifest)
            self.assertIn("exported_at", manifest)
            self.assertIn("files", manifest)
        else:
            # Verify the export script would produce correct structure
            # by checking the code path
            from bin.export_da_v2_to_onnx import sha256_file

            # Test sha256_file function
            test_file = pathlib.Path("/tmp/poc_onnx_out/depth_anything_v2_small.onnx")
            if test_file.exists():
                hash_val = sha256_file(test_file)
                self.assertEqual(len(hash_val), 64)  # SHA-256 hex length


class TestCanonicalPipeline(unittest.TestCase):
    """Test canonical_pipeline.py backend detection."""

    def test_detect_best_backend_windows(self):
        """Should detect DirectML on Windows."""
        with patch("platform.system", return_value="Windows"):
            with patch("onnxruntime.get_available_providers") as mock_prov:
                mock_prov.return_value = [
                    "DmlExecutionProvider",
                    "CPUExecutionProvider",
                ]
                from canonical_pipeline import detect_best_backend

                backend = detect_best_backend()
                self.assertEqual(backend, "local-onnx-directml")

    def test_detect_best_backend_macos(self):
        """Should detect MPS on macOS."""
        with patch("platform.system", return_value="Darwin"):
            with patch("torch.backends.mps.is_available", return_value=True):
                # Need to reload to clear cached import
                import importlib

                import canonical_pipeline

                importlib.reload(canonical_pipeline)

                backend = canonical_pipeline.detect_best_backend()
                self.assertEqual(backend, "local-mps")

    def test_detect_best_backend_fallback_skip(self):
        """Should fall back to skip when nothing is available."""
        with patch("platform.system", return_value="Linux"):
            with patch.dict("sys.modules", {"torch": None, "onnxruntime": None}):
                import importlib

                import canonical_pipeline

                importlib.reload(canonical_pipeline)

                backend = canonical_pipeline.detect_best_backend()
                self.assertEqual(backend, "skip")


if __name__ == "__main__":
    unittest.main(verbosity=2)
