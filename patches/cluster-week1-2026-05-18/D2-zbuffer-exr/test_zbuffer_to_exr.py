#!/usr/bin/env python3
"""
Tests for zbuffer_to_exr.py
"""

import json
import numpy as np
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add local directory to path for the zbuffer_to_exr module
import sys
sys.path.insert(0, str(Path(__file__).parent))

from zbuffer_to_exr import (
    parse_bin_header,
    read_bin_data,
    load_tick_timestamps,
    load_camera_frames,
    find_nearest_tick,
    zbuffer_to_exr
)


class TestZBufferToEXR(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary directory for test fixtures
        self.temp_dir = tempfile.mkdtemp()
        self.session_dir = Path(self.temp_dir) / 'test_session'
        self.session_dir.mkdir()
        
        # Create subdirectories
        (self.session_dir / 'zbuffer').mkdir()
        (self.session_dir / 'depth').mkdir()
        
    def tearDown(self):
        # Clean up temporary directory
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def create_test_bin_file(self, tick_id: int, width: int = 8, height: int = 8,
                            fill_value: float = 1.0) -> Path:
        """Create a test zbuffer .bin file."""
        bin_path = self.session_dir / 'zbuffer' / f'tick_{tick_id:06d}.bin'
        
        # Create header
        header = struct.pack('<III', width, height, tick_id)
        
        # Create depth data
        depth_data = np.full((height, width), fill_value, dtype=np.float32)
        
        with open(bin_path, 'wb') as f:
            f.write(header)
            f.write(depth_data.tobytes())
        
        return bin_path
    
    def test_parse_bin_header(self):
        """Test parsing .bin file header."""
        # Create a test file
        bin_path = self.session_dir / 'test.bin'
        with open(bin_path, 'wb') as f:
            f.write(struct.pack('<III', 1920, 1080, 12345))
        
        width, height, tick_id = parse_bin_header(bin_path)
        self.assertEqual(width, 1920)
        self.assertEqual(height, 1080)
        self.assertEqual(tick_id, 12345)
    
    def test_parse_bin_header_invalid(self):
        """Test parsing invalid header."""
        bin_path = self.session_dir / 'test.bin'
        with open(bin_path, 'wb') as f:
            f.write(b'too short')
        
        with self.assertRaises(ValueError):
            parse_bin_header(bin_path)
    
    def test_read_bin_data(self):
        """Test reading depth data from .bin file."""
        width, height = 4, 4
        tick_id = 1
        
        # Create test data
        test_data = np.array([
            [1.0, 2.0, 3.0, 4.0],
            [5.0, 6.0, 7.0, 8.0],
            [9.0, 10.0, 11.0, 12.0],
            [13.0, 14.0, 15.0, 16.0]
        ], dtype=np.float32)
        
        # Create file
        bin_path = self.session_dir / 'test.bin'
        with open(bin_path, 'wb') as f:
            f.write(struct.pack('<III', width, height, tick_id))
            f.write(test_data.tobytes())
        
        # Read and verify
        result = read_bin_data(bin_path, width, height)
        np.testing.assert_array_equal(result, test_data)
    
    def test_read_bin_data_size_mismatch(self):
        """Test reading .bin file with wrong size."""
        width, height = 4, 4
        tick_id = 1
        
        bin_path = self.session_dir / 'test.bin'
        with open(bin_path, 'wb') as f:
            f.write(struct.pack('<III', width, height, tick_id))
            f.write(b'not enough data')
        
        with self.assertRaises(ValueError):
            read_bin_data(bin_path, width, height)
    
    def test_load_tick_timestamps(self):
        """Test loading tick timestamps from game_state.jsonl."""
        game_state_path = self.session_dir / 'game_state.jsonl'
        
        # Create test data
        with open(game_state_path, 'w') as f:
            f.write(json.dumps({'tick_id': 1, 'timestamp_ms': 1000}) + '\n')
            f.write(json.dumps({'tick_id': 2, 'timestamp_ms': 1050}) + '\n')
            f.write(json.dumps({'tick_id': 3, 'timestamp_ms': 1100}) + '\n')
            # Invalid line
            f.write('invalid json\n')
            # Missing required fields
            f.write(json.dumps({'other_field': 'value'}) + '\n')
        
        timestamps = load_tick_timestamps(game_state_path)
        
        self.assertEqual(timestamps, {
            1: 1000,
            2: 1050,
            3: 1100
        })
    
    def test_load_tick_timestamps_missing_file(self):
        """Test loading tick timestamps when file doesn't exist."""
        game_state_path = self.session_dir / 'nonexistent.jsonl'
        timestamps = load_tick_timestamps(game_state_path)
        self.assertEqual(timestamps, {})
    
    def test_load_camera_frames(self):
        """Test loading camera frames from action_camera_*.jsonl."""
        # Create multiple camera files
        for i in range(2):
            camera_path = self.session_dir / f'action_camera_{i}.jsonl'
            with open(camera_path, 'w') as f:
                for frame_id in range(i * 3, (i + 1) * 3):
                    f.write(json.dumps({
                        'frame_id': frame_id,
                        'timestamp_ns': frame_id * 16666667  # ~60 fps
                    }) + '\n')
        
        frames = load_camera_frames(self.session_dir)
        
        # Should have 6 frames total, sorted by frame_id
        self.assertEqual(len(frames), 6)
        self.assertEqual([f[0] for f in frames], [0, 1, 2, 3, 4, 5])
    
    def test_load_camera_frames_no_files(self):
        """Test loading camera frames when no files exist."""
        frames = load_camera_frames(self.session_dir)
        self.assertEqual(frames, [])
    
    def test_find_nearest_tick(self):
        """Test finding nearest tick for a camera frame timestamp."""
        tick_timestamps = {
            1: 1000,  # 1.000s
            2: 1050,  # 1.050s
            3: 1100,  # 1.100s
        }
        
        # Test exact match
        result = find_nearest_tick(1050 * 1_000_000, tick_timestamps, max_gap_ms=50)
        self.assertIsNotNone(result)
        tick_id, gap_ms = result
        self.assertEqual(tick_id, 2)
        self.assertEqual(gap_ms, 0)
        
        # Test near match (within gap)
        result = find_nearest_tick(1060 * 1_000_000, tick_timestamps, max_gap_ms=50)
        self.assertIsNotNone(result)
        tick_id, gap_ms = result
        self.assertEqual(tick_id, 2)
        self.assertEqual(gap_ms, 10)
        
        # Test outside gap
        result = find_nearest_tick(1200 * 1_000_000, tick_timestamps, max_gap_ms=50)
        self.assertIsNone(result)
        
        # Test before first tick
        result = find_nearest_tick(900 * 1_000_000, tick_timestamps, max_gap_ms=50)
        self.assertIsNone(result)
    
    def test_zbuffer_to_exr_no_zbuffer_dir(self):
        """Test when zbuffer directory doesn't exist."""
        # Remove zbuffer directory
        import shutil
        shutil.rmtree(self.session_dir / 'zbuffer')
        
        # Should return True (graceful skip)
        result = zbuffer_to_exr(self.session_dir)
        self.assertTrue(result)
        
        # No .source file should be created
        source_path = self.session_dir / 'depth' / '.source'
        self.assertFalse(source_path.exists())
    
    def test_zbuffer_to_exr_no_zbuffer_files(self):
        """Test when zbuffer directory exists but has no .bin files."""
        # Should return True (graceful skip)
        result = zbuffer_to_exr(self.session_dir)
        self.assertTrue(result)
        
        # No .source file should be created
        source_path = self.session_dir / 'depth' / '.source'
        self.assertFalse(source_path.exists())
    
    @patch('zbuffer_to_exr.OPENEXR_AVAILABLE', False)
    def test_zbuffer_to_exr_openexr_not_available(self):
        """Test when OpenEXR is not available."""
        # Create a test bin file
        self.create_test_bin_file(tick_id=1)
        
        # Should return True (graceful skip)
        result = zbuffer_to_exr(self.session_dir)
        self.assertTrue(result)
        
        # No .source file should be created
        source_path = self.session_dir / 'depth' / '.source'
        self.assertFalse(source_path.exists())
    
    @patch('zbuffer_to_exr.OPENEXR_AVAILABLE', True)
    @patch('zbuffer_to_exr.write_exr')
    def test_zbuffer_to_exr_integration(self, mock_write_exr):
        """Integration test with mocked EXR writing."""
        # Create zbuffer files
        for tick_id in [1, 2, 3]:
            self.create_test_bin_file(tick_id=tick_id, fill_value=float(tick_id))
        
        # Create game_state.jsonl
        game_state_path = self.session_dir / 'game_state.jsonl'
        with open(game_state_path, 'w') as f:
            for tick_id in [1, 2, 3]:
                f.write(json.dumps({
                    'tick_id': tick_id,
                    'timestamp_ms': 1000 + (tick_id - 1) * 50  # 20 Hz = 50ms intervals
                }) + '\n')
        
        # Create action_camera_0.jsonl
        camera_path = self.session_dir / 'action_camera_0.jsonl'
        with open(camera_path, 'w') as f:
            for frame_id in range(1, 6):
                # Frame timestamps at 60 fps (~16.67ms intervals)
                timestamp_ns = 1_000_000_000 + (frame_id - 1) * 16_666_667
                f.write(json.dumps({
                    'frame_id': frame_id,
                    'timestamp_ns': timestamp_ns
                }) + '\n')
        
        # Run conversion
        result = zbuffer_to_exr(self.session_dir, max_gap_ms=50)
        self.assertTrue(result)
        
        # Check that .source file was created
        source_path = self.session_dir / 'depth' / '.source'
        self.assertTrue(source_path.exists())
        
        with open(source_path, 'r') as f:
            source_data = json.load(f)
        
        self.assertEqual(source_data['kind'], 'engine_zbuffer')
        self.assertEqual(source_data['framerate'], 60)
        self.assertEqual(source_data['max_depth_m'], 256.0)
        self.assertEqual(source_data['calibrated'], True)
        self.assertEqual(source_data['frame_count'], 5)
        self.assertEqual(source_data['alignment_method'], 'nearest_tick_50ms')
        # All frames should align within 50ms
        self.assertEqual(source_data['gap_misses'], 0)
        self.assertEqual(source_data['gap_miss_ratio'], '0/5')
        
        # Check that write_exr was called for aligned frames
        # Should be called 5 times (all frames align within 50ms)
        self.assertEqual(mock_write_exr.call_count, 5)
    
    @patch('zbuffer_to_exr.OPENEXR_AVAILABLE', True)
    @patch('zbuffer_to_exr.write_exr')
    def test_zbuffer_to_exr_with_gap_misses(self, mock_write_exr):
        """Test when some frames have gap > max_gap_ms."""
        # Create zbuffer files
        for tick_id in [1, 3]:  # Skip tick 2 to create a gap
            self.create_test_bin_file(tick_id=tick_id)
        
        # Create game_state.jsonl with sparse ticks
        game_state_path = self.session_dir / 'game_state.jsonl'
        with open(game_state_path, 'w') as f:
            f.write(json.dumps({'tick_id': 1, 'timestamp_ms': 1000}) + '\n')
            f.write(json.dumps({'tick_id': 3, 'timestamp_ms': 1100}) + '\n')  # 100ms gap
        
        # Create camera frames
        camera_path = self.session_dir / 'action_camera_0.jsonl'
        with open(camera_path, 'w') as f:
            # Frame at 1050ms - between ticks 1 and 3, 50ms from tick 1, 50ms from tick 3
            # With max_gap_ms=50, this should align with tick 1 (exactly 50ms gap)
            f.write(json.dumps({
                'frame_id': 1,
                'timestamp_ns': 1_050_000_000  # 1050ms
            }) + '\n')
            
            # Frame at 1150ms - 50ms from tick 3, 150ms from tick 1
            # Should align with tick 3 (50ms gap)
            f.write(json.dumps({
                'frame_id': 2,
                'timestamp_ns': 1_150_000_000  # 1150ms
            }) + '\n')
            
            # Frame at 1200ms - 100ms from tick 3, 200ms from tick 1
            # Should NOT align (gap > 50ms)
            f.write(json.dumps({
                'frame_id': 3,
                'timestamp_ns': 1_200_000_000  # 1200ms
            }) + '\n')
        
        # Run conversion with strict 50ms max gap
        result = zbuffer_to_exr(self.session_dir, max_gap_ms=50)
        self.assertTrue(result)
        
        # Check .source file
        source_path = self.session_dir / 'depth' / '.source'
        self.assertTrue(source_path.exists())
        
        with open(source_path, 'r') as f:
            source_data = json.load(f)
        
        # Should have 1 gap miss (frame 3 at 1200ms doesn't align)
        self.assertEqual(source_data['gap_misses'], 1)
        self.assertEqual(source_data['gap_miss_ratio'], '1/3')
        
        # write_exr should be called 2 times (frames 1 and 2 align)
        self.assertEqual(mock_write_exr.call_count, 2)


if __name__ == '__main__':
    unittest.main()
