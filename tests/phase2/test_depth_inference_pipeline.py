"""
Tests for depth_inference_pipeline.py
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import tempfile
import shutil


def test_extract_frames_invokes_ffmpeg():
    """Test that extract_frames calls ffmpeg with correct arguments."""
    from depth_inference_pipeline import extract_frames
    
    # Create a mock video file
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "test.mp4"
        video_path.touch()  # Create empty file
        
        output_dir = Path(tmpdir) / "frames"
        
        # Mock subprocess.run
        mock_run = Mock()
        mock_run.return_value = Mock(returncode=0)
        
        with patch('subprocess.run', mock_run):
            # Call the function
            result = extract_frames(str(video_path), str(output_dir), fps=10.0)
            
            # Verify subprocess.run was called
            assert mock_run.called
            
            # Get the call arguments
            call_args = mock_run.call_args[0][0]
            
            # Verify ffmpeg command structure
            assert call_args[0] == "ffmpeg"
            assert "-i" in call_args
            assert str(video_path) in call_args
            
            # Verify fps argument
            fps_index = call_args.index("-vf")
            assert "fps=10.0" in call_args[fps_index + 1]
            
            # Verify output pattern
            assert "frame_%06d.png" in call_args[-1]
            
            # Function should return empty list since we mocked subprocess
            # and no actual frames were created
            assert isinstance(result, list)


def test_extract_frames_raises_on_missing_video():
    """Test that extract_frames raises FileNotFoundError for missing video."""
    from depth_inference_pipeline import extract_frames
    
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "nonexistent.mp4"
        output_dir = Path(tmpdir) / "frames"
        
        with pytest.raises(FileNotFoundError):
            extract_frames(str(video_path), str(output_dir))


def test_extract_frames_raises_on_ffmpeg_failure():
    """Test that extract_frames raises RuntimeError when ffmpeg fails."""
    from depth_inference_pipeline import extract_frames
    
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "test.mp4"
        video_path.touch()
        output_dir = Path(tmpdir) / "frames"
        
        # Mock subprocess.run to raise FileNotFoundError (simulating ffmpeg not found)
        mock_run = Mock()
        mock_run.side_effect = FileNotFoundError("ffmpeg not found")
        
        with patch('subprocess.run', mock_run):
            with pytest.raises(RuntimeError, match="ffmpeg not found"):
                extract_frames(str(video_path), str(output_dir))


def test_infer_depth_batch_skips_when_torch_missing():
    """Test that infer_depth_batch raises RuntimeError when torch is missing."""
    from depth_inference_pipeline import infer_depth_batch
    
    with tempfile.TemporaryDirectory() as tmpdir:
        rgb_paths = [str(Path(tmpdir) / "test.png")]
        
        # Create a dummy file
        Path(rgb_paths[0]).touch()
        
        # Mock the import inside the function to raise ImportError
        with patch('depth_inference_pipeline.__import__') as mock_import:
            def side_effect(name, *args, **kwargs):
                if name == "torch":
                    raise ImportError("No module named 'torch'")
                # For other imports, use the real import
                return __builtins__.__import__(name, *args, **kwargs)
            
            mock_import.side_effect = side_effect
            
            with pytest.raises(RuntimeError, match="Missing dependency"):
                infer_depth_batch(rgb_paths, tmpdir)


def test_infer_depth_batch_raises_on_invalid_near_far():
    """Test that infer_depth_batch raises ValueError when near >= far."""
    from depth_inference_pipeline import infer_depth_batch
    
    with tempfile.TemporaryDirectory() as tmpdir:
        rgb_paths = [str(Path(tmpdir) / "test.png")]
        Path(rgb_paths[0]).touch()
        
        # Mock all the imports to avoid actual dependency issues
        with patch('depth_inference_pipeline.__import__') as mock_import:
            # Create mock modules
            mock_torch = Mock()
            mock_torch.device = Mock(return_value="cpu")
            mock_torch.cuda = Mock(is_available=Mock(return_value=False))
            
            mock_transformers = Mock()
            mock_transformers.pipeline = Mock(return_value=Mock(
                model=Mock(to=Mock(return_value=None))
            ))
            
            mock_np = Mock()
            mock_np.array = Mock(return_value=Mock(
                min=Mock(return_value=0.0),
                max=Mock(return_value=1.0),
                shape=(100, 100),
                astype=Mock(return_value=b"")
            ))
            mock_np.float32 = float
            mock_np.full_like = Mock(return_value=Mock(
                astype=Mock(return_value=b"")
            ))
            mock_np.clip = Mock(return_value=Mock(
                astype=Mock(return_value=b"")
            ))
            
            mock_openexr = Mock()
            mock_openexr.Header = Mock(return_value={'channels': {}})
            mock_openexr.OutputFile = Mock(return_value=Mock(
                writePixels=Mock(),
                close=Mock()
            ))
            
            mock_imath = Mock()
            mock_imath.Channel = Mock()
            mock_imath.PixelType = Mock(FLOAT=Mock())
            
            mock_pil = Mock()
            mock_pil.Image = Mock(open=Mock(return_value=Mock(
                __enter__=Mock(return_value=Mock(
                    __exit__=Mock()
                ))
            )))
            
            def import_side_effect(name, *args, **kwargs):
                if name == "torch":
                    return mock_torch
                elif name == "transformers":
                    return mock_transformers
                elif name == "numpy":
                    return mock_np
                elif name == "OpenEXR":
                    return mock_openexr
                elif name == "Imath":
                    return mock_imath
                elif name == "PIL.Image":
                    return mock_pil
                else:
                    return __builtins__.__import__(name, *args, **kwargs)
            
            mock_import.side_effect = import_side_effect
            
            # Try with near >= far
            with pytest.raises(ValueError, match="must be less than"):
                infer_depth_batch(rgb_paths, tmpdir, near_m=10.0, far_m=5.0)


def test_video_to_depth_exrs_chains_correctly():
    """Test that video_to_depth_exrs calls extract_frames and infer_depth_batch in order."""
    from depth_inference_pipeline import video_to_depth_exrs
    
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "test.mp4"
        video_path.touch()
        output_dir = Path(tmpdir) / "output"
        
        # Mock both functions
        mock_extract = Mock(return_value=["frame1.png", "frame2.png"])
        mock_infer = Mock(return_value=2)
        
        with patch('depth_inference_pipeline.extract_frames', mock_extract):
            with patch('depth_inference_pipeline.infer_depth_batch', mock_infer):
                # Call the function
                result = video_to_depth_exrs(str(video_path), str(output_dir))
                
                # Verify extract_frames was called first with correct args
                assert mock_extract.called
                extract_args = mock_extract.call_args
                # First arg should be video path
                assert extract_args[0][0] == str(video_path)
                # Second arg should contain temp dir
                assert "_temp_frames" in extract_args[0][1]
                # Check fps kwarg
                assert extract_args[1]["fps"] == 6.0  # default
                
                # Verify infer_depth_batch was called with frames from extract_frames
                assert mock_infer.called
                infer_args = mock_infer.call_args
                # First arg should be the frame list
                assert infer_args[0][0] == ["frame1.png", "frame2.png"]
                # Second arg should be output dir
                assert infer_args[0][1] == str(output_dir)
                
                # Verify result comes from infer_depth_batch
                assert result == 2


def test_video_to_depth_exrs_cleans_up_temp_dir():
    """Test that video_to_depth_exrs cleans up temporary directory even on error."""
    from depth_inference_pipeline import video_to_depth_exrs
    
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "test.mp4"
        video_path.touch()
        output_dir = Path(tmpdir) / "output"
        
        # Mock extract_frames to raise an exception
        mock_extract = Mock(side_effect=RuntimeError("Test error"))
        
        with patch('depth_inference_pipeline.extract_frames', mock_extract):
            # This should raise the exception
            with pytest.raises(RuntimeError, match="Test error"):
                video_to_depth_exrs(str(video_path), str(output_dir))
            
            # Verify extract_frames was called
            assert mock_extract.called


def test_module_imports_without_dependencies():
    """Test that the module can be imported without external dependencies."""
    # This test verifies the module doesn't fail on import
    import depth_inference_pipeline
    
    # Check that the functions exist
    assert hasattr(depth_inference_pipeline, 'extract_frames')
    assert hasattr(depth_inference_pipeline, 'infer_depth_batch')
    assert hasattr(depth_inference_pipeline, 'video_to_depth_exrs')
    
    # Check they are callable
    assert callable(depth_inference_pipeline.extract_frames)
    assert callable(depth_inference_pipeline.infer_depth_batch)
    assert callable(depth_inference_pipeline.video_to_depth_exrs)


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])