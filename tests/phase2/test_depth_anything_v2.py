"""
Tests for depth_anything_v2 module.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "oyster_agent_runner" / "phase2"))
import os
import tempfile
from unittest.mock import Mock, patch

import numpy as np
import pytest


class TestDepthAnythingV2:
    """Test suite for depth_anything_v2 module."""

    def test_module_imports_without_torch(self, monkeypatch):
        """Verify the module loads even if torch is unavailable (lazy import)."""
        # Remove torch from sys.modules to simulate it not being installed
        original_torch = sys.modules.get('torch')
        original_transformers = sys.modules.get('transformers')
        original_imageio = sys.modules.get('imageio')

        # Temporarily remove these modules
        if 'torch' in sys.modules:
            monkeypatch.delitem(sys.modules, 'torch')
        if 'transformers' in sys.modules:
            monkeypatch.delitem(sys.modules, 'transformers')
        if 'imageio' in sys.modules:
            monkeypatch.delitem(sys.modules, 'imageio')

        try:
            # This should not raise an ImportError
            from depth_anything_v2 import infer_depth, is_available
            # Module imported successfully
            assert True
        except ImportError as e:
            pytest.fail(f"Module failed to import without torch: {e}")
        finally:
            # Restore original modules
            if original_torch is not None:
                sys.modules['torch'] = original_torch
            if original_transformers is not None:
                sys.modules['transformers'] = original_transformers
            if original_imageio is not None:
                sys.modules['imageio'] = original_imageio

    def test_is_available_returns_false_when_torch_missing(self, monkeypatch):
        """Test is_available() returns False when torch is missing."""
        # Remove torch from sys.modules
        original_torch = sys.modules.get('torch')
        if 'torch' in sys.modules:
            monkeypatch.delitem(sys.modules, 'torch')

        try:
            # Import the module fresh
            import importlib

            import depth_anything_v2
            importlib.reload(depth_anything_v2)

            # is_available should return False
            assert depth_anything_v2.is_available() == False
        finally:
            # Restore torch
            if original_torch is not None:
                sys.modules['torch'] = original_torch

    def test_is_available_returns_true_when_all_present(self, monkeypatch):
        """Test is_available() returns True when all dependencies are present."""
        # Mock all three lazy imports
        mock_torch = Mock()
        mock_transformers = Mock()
        mock_imageio = Mock()

        monkeypatch.setitem(sys.modules, 'torch', mock_torch)
        monkeypatch.setitem(sys.modules, 'transformers', mock_transformers)
        monkeypatch.setitem(sys.modules, 'imageio', mock_imageio)

        # Import the module fresh
        import importlib

        import depth_anything_v2
        importlib.reload(depth_anything_v2)

        # is_available should return True
        assert depth_anything_v2.is_available() == True

    def test_infer_depth_returns_false_when_unavailable(self, monkeypatch):
        """Test infer_depth returns False when is_available() is False."""
        # Mock is_available to return False
        with patch('depth_anything_v2.is_available', return_value=False):
            from depth_anything_v2 import infer_depth

            # Create a temporary input file
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                input_path = tmp.name

            output_path = input_path.replace('.png', '_depth.exr')

            try:
                # infer_depth should return False without calling torch
                result = infer_depth(input_path, output_path)
                assert result == False
            finally:
                # Clean up
                if os.path.exists(input_path):
                    os.unlink(input_path)
                if os.path.exists(output_path):
                    os.unlink(output_path)

    def test_infer_depth_writes_exr_when_pipeline_works(self, monkeypatch, tmp_path):
        """Test infer_depth writes EXR file when pipeline works."""
        # Create mock dependencies
        mock_torch = Mock()
        mock_transformers = Mock()
        mock_imageio = Mock()

        # Mock imageio.imread to return a dummy image
        dummy_image = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
        mock_imageio.imread.return_value = dummy_image

        # Mock the pipeline
        mock_pipeline = Mock()
        mock_pipeline.return_value = {
            'depth': np.random.rand(100, 100).astype(np.float32)
        }

        # Mock transformers pipeline creation
        mock_transformers.pipeline.return_value = mock_pipeline

        # Mock torch operations
        mock_torch.from_numpy = lambda x: x  # Just return the numpy array
        mock_torch.device = Mock()

        # Set up sys.modules
        monkeypatch.setitem(sys.modules, 'torch', mock_torch)
        monkeypatch.setitem(sys.modules, 'transformers', mock_transformers)
        monkeypatch.setitem(sys.modules, 'imageio', mock_imageio)

        # Import the module fresh
        import importlib

        import depth_anything_v2
        importlib.reload(depth_anything_v2)

        # Create test files
        input_path = tmp_path / "test.png"
        output_path = tmp_path / "test_depth.exr"

        # Create a dummy image file
        with open(input_path, 'wb') as f:
            f.write(b'dummy png data')

        # Mock imageio.imwrite for EXR
        mock_imwrite_called = []
        def mock_imwrite(path, data, format=None):
            mock_imwrite_called.append((path, data, format))

        mock_imageio.imwrite = mock_imwrite

        # Call infer_depth
        result = depth_anything_v2.infer_depth(str(input_path), str(output_path))

        # Verify results
        assert result == True

        # Verify imageio.imread was called
        mock_imageio.imread.assert_called_once_with(str(input_path))

        # Verify pipeline was created
        mock_transformers.pipeline.assert_called_once()

        # Verify pipeline was called
        mock_pipeline.assert_called_once()

        # Verify imwrite was called with EXR format
        assert len(mock_imwrite_called) == 1
        path, data, fmt = mock_imwrite_called[0]
        assert str(path) == str(output_path)
        assert fmt == 'EXR-FI'
        # Check that data has 'Z' channel and is float32
        assert 'Z' in data
        assert data['Z'].dtype == np.float32
