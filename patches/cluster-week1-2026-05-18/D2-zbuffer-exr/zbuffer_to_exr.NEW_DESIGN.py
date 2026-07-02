#!/usr/bin/env python3
"""
Convert zbuffer .bin files to OpenEXR depth maps aligned with camera frames.

Usage:
    python3 bin/zbuffer_to_exr.py <session_dir> [--max-gap-ms 50] [--fallback-on-miss true]
"""

import argparse
import json
import logging
import numpy as np
import os
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import OpenEXR
    import Imath
    OPENEXR_AVAILABLE = True
except ImportError:
    OPENEXR_AVAILABLE = False
    logging.warning("OpenEXR not available. EXR output will be skipped.")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def parse_bin_header(bin_path: Path) -> Tuple[int, int, int]:
    """Parse zbuffer .bin file header (12 bytes: width, height, tick_id)."""
    with open(bin_path, 'rb') as f:
        header = f.read(12)
        if len(header) != 12:
            raise ValueError(f"Invalid header size: {len(header)} bytes")
        width, height, tick_id = struct.unpack('<III', header)
        return width, height, tick_id


def read_bin_data(bin_path: Path, width: int, height: int) -> np.ndarray:
    """Read depth data from .bin file (after 12-byte header)."""
    file_size = os.path.getsize(bin_path)
    expected_size = 12 + width * height * 4  # 4 bytes per float32
    
    if file_size != expected_size:
        raise ValueError(f"File size mismatch: expected {expected_size}, got {file_size}")
    
    with open(bin_path, 'rb') as f:
        f.seek(12)  # Skip header
        data = f.read(width * height * 4)
        # Convert to numpy array of float32
        depth_array = np.frombuffer(data, dtype=np.float32).reshape((height, width))
        return depth_array


def load_tick_timestamps(game_state_path: Path) -> Dict[int, int]:
    """Load tick_id -> timestamp_ms mapping from game_state.jsonl."""
    tick_timestamps = {}
    
    if not game_state_path.exists():
        logger.warning(f"game_state.jsonl not found at {game_state_path}")
        return tick_timestamps
    
    with open(game_state_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                tick_id = record.get('tick_id')
                timestamp_ms = record.get('timestamp_ms')
                if tick_id is not None and timestamp_ms is not None:
                    tick_timestamps[tick_id] = timestamp_ms
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON line: {line}")
    
    return tick_timestamps


def load_camera_frames(session_dir: Path) -> List[Tuple[int, int]]:
    """Load camera frame_id -> timestamp_ns from action_camera_*.jsonl files."""
    camera_frames = []
    
    # Find all action_camera_*.jsonl files
    camera_files = list(session_dir.glob('action_camera_*.jsonl'))
    if not camera_files:
        logger.warning(f"No action_camera_*.jsonl files found in {session_dir}")
        return camera_frames
    
    for camera_file in camera_files:
        with open(camera_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    frame_id = record.get('frame_id')
                    timestamp_ns = record.get('timestamp_ns')
                    if frame_id is not None and timestamp_ns is not None:
                        camera_frames.append((frame_id, timestamp_ns))
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse JSON line in {camera_file}: {line}")
    
    # Sort by frame_id
    camera_frames.sort(key=lambda x: x[0])
    return camera_frames


def find_nearest_tick(timestamp_ns: int, tick_timestamps: Dict[int, int], max_gap_ms: int) -> Optional[Tuple[int, int]]:
    """Find nearest tick for given camera frame timestamp.
    
    Returns (tick_id, gap_ms) if found within max_gap_ms, None otherwise.
    """
    timestamp_ms = timestamp_ns // 1_000_000  # Convert ns to ms
    
    # Convert tick_timestamps dict to sorted list for binary search
    tick_items = sorted(tick_timestamps.items(), key=lambda x: x[1])
    tick_times = [t[1] for t in tick_items]
    
    # Binary search for nearest timestamp
    import bisect
    idx = bisect.bisect_left(tick_times, timestamp_ms)
    
    candidates = []
    if idx > 0:
        candidates.append((idx - 1, abs(tick_times[idx - 1] - timestamp_ms)))
    if idx < len(tick_times):
        candidates.append((idx, abs(tick_times[idx] - timestamp_ms)))
    
    if not candidates:
        return None
    
    # Find minimum gap
    best_idx, best_gap = min(candidates, key=lambda x: x[1])
    
    if best_gap <= max_gap_ms:
        tick_id, tick_time = tick_items[best_idx]
        return tick_id, best_gap
    
    return None


def write_exr(depth_array: np.ndarray, output_path: Path, max_depth_m: float = 256.0):
    """Write depth array to OpenEXR file as 16-bit half float."""
    if not OPENEXR_AVAILABLE:
        raise RuntimeError("OpenEXR not available")
    
    height, width = depth_array.shape
    
    # Convert to half float
    import numpy as np
    depth_half = depth_array.astype(np.float16)
    
    # Create EXR header
    header = OpenEXR.Header(width, height)
    
    # Set pixel type to HALF
    half_type = Imath.PixelType(Imath.PixelType.HALF)
    header['channels'] = {'Z': Imath.Channel(half_type)}
    
    # Write EXR file
    exr_file = OpenEXR.OutputFile(str(output_path), header)
    
    # Convert to bytes
    z_data = depth_half.tobytes()
    exr_file.writePixels({'Z': z_data})
    exr_file.close()


def zbuffer_to_exr(
    session_dir: Path,
    max_gap_ms: int = 50,
    fallback_on_miss: bool = True
) -> bool:
    """Main conversion function.
    
    Returns True if conversion succeeded (or was skipped gracefully),
    False if fatal error occurred.
    """
    session_dir = Path(session_dir)
    
    # Check if zbuffer directory exists
    zbuffer_dir = session_dir / 'zbuffer'
    if not zbuffer_dir.exists() or not list(zbuffer_dir.glob('tick_*.bin')):
        logger.info(f"No zbuffer files found in {zbuffer_dir}, skipping conversion")
        return True  # Graceful skip
    
    # Check OpenEXR availability
    if not OPENEXR_AVAILABLE:
        logger.warning("OpenEXR not available. Skipping zbuffer to EXR conversion.")
        return True  # Graceful skip
    
    # Load tick timestamps
    game_state_path = session_dir / 'game_state.jsonl'
    tick_timestamps = load_tick_timestamps(game_state_path)
    if not tick_timestamps:
        logger.warning(f"No tick timestamps found in {game_state_path}")
        return True  # Graceful skip
    
    # Load camera frames
    camera_frames = load_camera_frames(session_dir)
    if not camera_frames:
        logger.warning(f"No camera frames found in {session_dir}")
        return True  # Graceful skip
    
    # Create output directory
    depth_dir = session_dir / 'depth'
    depth_dir.mkdir(exist_ok=True)
    
    # Load all zbuffer files
    zbuffer_files = {}
    for bin_file in zbuffer_dir.glob('tick_*.bin'):
        try:
            width, height, tick_id = parse_bin_header(bin_file)
            zbuffer_files[tick_id] = (bin_file, width, height)
        except Exception as e:
            logger.warning(f"Failed to parse header of {bin_file}: {e}")
            continue
    
    if not zbuffer_files:
        logger.warning(f"No valid zbuffer files found in {zbuffer_dir}")
        return True  # Graceful skip
    
    # Process each camera frame
    gap_misses = 0
    processed_frames = 0
    
    for frame_id, timestamp_ns in camera_frames:
        # Find nearest tick
        result = find_nearest_tick(timestamp_ns, tick_timestamps, max_gap_ms)
        
        if result is None:
            gap_misses += 1
            if fallback_on_miss:
                logger.debug(f"Frame {frame_id}: no tick within {max_gap_ms}ms, skipping (will use DA-V2 fallback)")
            continue
        
        tick_id, gap_ms = result
        
        # Check if we have zbuffer data for this tick
        if tick_id not in zbuffer_files:
            gap_misses += 1
            logger.warning(f"Frame {frame_id}: tick {tick_id} found but no zbuffer file")
            continue
        
        # Load and write EXR
        bin_file, width, height = zbuffer_files[tick_id]
        try:
            depth_array = read_bin_data(bin_file, width, height)
            
            # Write EXR
            output_path = depth_dir / f'frame_{frame_id:06d}.exr'
            write_exr(depth_array, output_path)
            
            processed_frames += 1
            if processed_frames % 100 == 0:
                logger.info(f"Processed {processed_frames} frames...")
                
        except Exception as e:
            gap_misses += 1
            logger.warning(f"Frame {frame_id}: failed to process zbuffer for tick {tick_id}: {e}")
            continue
    
    # Write .source file
    source_data = {
        "kind": "engine_zbuffer",
        "framerate": 60,
        "max_depth_m": 256.0,
        "calibrated": True,
        "frame_count": len(camera_frames),
        "alignment_method": f"nearest_tick_{max_gap_ms}ms",
        "gap_misses": gap_misses,
        "gap_miss_ratio": f"{gap_misses}/{len(camera_frames)}"
    }
    
    source_path = depth_dir / '.source'
    with open(source_path, 'w') as f:
        json.dump(source_data, f, indent=2)
    
    logger.info(f"Conversion complete: {processed_frames} frames written, {gap_misses} gaps missed")
    return True


def main():
    parser = argparse.ArgumentParser(description='Convert zbuffer .bin files to OpenEXR depth maps')
    parser.add_argument('session_dir', type=str, help='Session directory path')
    parser.add_argument('--max-gap-ms', type=int, default=50, help='Maximum alignment gap in milliseconds')
    parser.add_argument('--fallback-on-miss', type=str, default='true',
                       choices=['true', 'false'], help='Whether to fallback to DA-V2 for missed frames')
    
    args = parser.parse_args()
    
    fallback_on_miss = args.fallback_on_miss.lower() == 'true'
    
    success = zbuffer_to_exr(
        Path(args.session_dir),
        max_gap_ms=args.max_gap_ms,
        fallback_on_miss=fallback_on_miss
    )
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
