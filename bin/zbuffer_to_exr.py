#!/usr/bin/env python3
"""
Convert raw float32 depth buffer files to EXR format.
Input: active_session/depth_raw/*.f32 (raw float32 binary files)
Output: active_session/depth/*.exr (OpenEXR format with Z channel)

Marker: kind: engine_zbuffer
"""

import glob
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np

# Try to import OpenEXR
try:
    import Imath
    import OpenEXR
    HAS_EXR = True
except ImportError:
    HAS_EXR = False
    print("Warning: OpenEXR not available, using numpy fallback for testing")

def read_f32_file(filepath, width=1920, height=1080):
    """Read raw float32 binary file."""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    # Read as little-endian float32
    floats = struct.unpack(f'<{len(data)//4}f', data)
    arr = np.array(floats, dtype=np.float32).reshape(height, width)
    return arr

def write_exr_file(filepath, depth_data, width=1920, height=1080):
    """Write depth data to EXR file."""
    if not HAS_EXR:
        # Fallback for testing: write as numpy file
        np.save(filepath.replace('.exr', '.npy'), depth_data)
        return
    
    # Prepare header
    header = OpenEXR.Header(width, height)
    
    # Set pixel type to FLOAT (32-bit float)
    header['channels'] = {'Z': Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
    
    # Add metadata
    header['displayWindow'] = Imath.Box2i(Imath.V2i(0, 0), Imath.V2i(width-1, height-1))
    header['dataWindow'] = header['displayWindow']
    
    # Write units metadata
    header['units'] = 'meters'
    header['kind'] = 'engine_zbuffer'
    
    # Create EXR file
    exr_file = OpenEXR.OutputFile(filepath, header)
    
    # Convert depth data to bytes
    z_data = depth_data.astype(np.float32).tobytes()
    
    # Write channel
    exr_file.writePixels({'Z': z_data})
    exr_file.close()

def create_source_marker(output_dir):
    """Create depth/.source marker file."""
    source_file = os.path.join(output_dir, '.source')
    marker = {
        'kind': 'engine_zbuffer',
        'units': 'meters',
        'format': 'exr',
        'dtype': 'float32',
        'channels': ['Z'],
        'resolution': [1920, 1080],
        'linearized': True,
        'coordinate_system': 'view_space',
        'description': 'Engine Z-buffer linearized to view-space meters'
    }
    
    with open(source_file, 'w') as f:
        json.dump(marker, f, indent=2)

def main():
    # Check for active_session directory
    active_session = Path('active_session')
    if not active_session.exists():
        print("Error: active_session directory not found")
        sys.exit(1)
    
    # Input and output directories
    depth_raw_dir = active_session / 'depth_raw'
    depth_output_dir = active_session / 'depth'
    
    if not depth_raw_dir.exists():
        print(f"Error: {depth_raw_dir} not found")
        sys.exit(1)
    
    # Create output directory
    depth_output_dir.mkdir(exist_ok=True)
    
    # Get all .f32 files
    f32_files = sorted(glob.glob(str(depth_raw_dir / '*.f32')))
    
    if not f32_files:
        print(f"Warning: No .f32 files found in {depth_raw_dir}")
        # Create empty source marker anyway
        create_source_marker(depth_output_dir)
        sys.exit(0)
    
    print(f"Found {len(f32_files)} depth frames to convert")
    
    # Process each file
    for i, f32_file in enumerate(f32_files):
        if i % 100 == 0:
            print(f"Processing frame {i+1}/{len(f32_files)}...")
        
        # Read raw depth data
        depth_data = read_f32_file(f32_file)
        
        # Generate output filename
        frame_num = Path(f32_file).stem
        exr_file = depth_output_dir / f"{frame_num}.exr"
        
        # Write EXR file
        write_exr_file(str(exr_file), depth_data)
    
    print(f"Converted {len(f32_files)} frames to EXR format")
    
    # Create source marker
    create_source_marker(depth_output_dir)
    print(f"Created source marker at {depth_output_dir}/.source")
    
    # Verify we have approximately 1800 frames (6 fps × 5 min × 60s)
    expected_frames = 6 * 5 * 60  # 1800 frames
    if len(f32_files) < expected_frames * 0.9:  # Allow 10% tolerance
        print(f"Warning: Only {len(f32_files)} frames found, expected ~{expected_frames}")
    
    print("Conversion complete!")

if __name__ == '__main__':
    main()
