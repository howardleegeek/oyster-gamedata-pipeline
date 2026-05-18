#!/usr/bin/env python3
"""
PRD p4 #5: Depth EXR 6fps alignment with 30fps video — frame index ratio 5:1 exact.
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

VIDEO_FPS = 30
DEPTH_FPS = 6
FRAME_RATIO = VIDEO_FPS // DEPTH_FPS  # 5


def natural_sort_key(path: Path) -> Tuple[int, ...]:
    """Sort key handling numeric sequences in filenames."""
    return tuple(int(m) if m.isdigit() else m for m in re.split(r'(\d+)', path.stem))


def find_frames(directory: Path, exts: List[str]) -> List[Path]:
    """Find all frame files with given extensions."""
    if not directory.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")
    if not directory.is_dir():
        # Check what it actually is
        if directory.is_file():
            raise NotADirectoryError(f"Path exists as a file, not a directory: {directory}")
        elif directory.is_symlink():
            raise NotADirectoryError(f"Path exists as a symlink, not a directory: {directory}")
        else:
            raise NotADirectoryError(f"Path exists but is not a directory: {directory}")
    files = []
    for ext in exts:
        files.extend(directory.glob(f"*{ext}"))
    return sorted(files, key=natural_sort_key)


def extract_index(filename: Path) -> int:
    """Extract trailing frame index from filename."""
    match = re.search(r'(\d+)$', filename.stem)
    if not match:
        raise ValueError(f"Cannot extract index from {filename}")
    return int(match.group(1))


def validate_alignment(video_dir: Path, depth_dir: Path, verbose: bool = False) -> Tuple[bool, str]:
    """Validate depth EXR 6fps aligns with 30fps video (5:1 ratio)."""
    video_frames = find_frames(video_dir, ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'])
    depth_frames = find_frames(depth_dir, ['.exr'])

    if not video_frames:
        return False, f"No video frames in {video_dir}"
    if not depth_frames:
        return False, f"No depth EXR frames in {depth_dir}"

    if verbose:
        print(f"Video: {len(video_frames)} frames, Depth: {len(depth_frames)} frames")

    video_idx = sorted({extract_index(f) for f in video_frames})
    depth_idx = sorted({extract_index(f) for f in depth_frames})

    # Each depth frame should map to video_idx = depth_idx * 5
    expected_video = {d * FRAME_RATIO for d in depth_idx}
    missing = [v for v in expected_video if v not in video_idx]

    if missing:
        return False, f"Missing depth alignment: {missing[:5]}"

    if verbose:
        print(f"✓ 5:1 ratio verified ({FRAME_RATIO}:1)")

    return True, f"Valid: {len(depth_idx)} depth @ 6fps → {len(video_idx)} video @ 30fps"


def main(argv: List[str] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Validate 6fps depth EXR alignment with 30fps video.")
    parser.add_argument('-v', '--video-dir', type=Path, required=True, help="Video frames directory (30fps)")
    parser.add_argument('-d', '--depth-dir', type=Path, required=True, help="Depth EXR directory (6fps)")
    parser.add_argument('--verbose', '-V', action='store_true', help="Verbose output")
    args = parser.parse_args(argv)

    try:
        ok, msg = validate_alignment(args.video_dir, args.depth_dir, args.verbose)
        print(msg)
        return 0 if ok else 1
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except NotADirectoryError as e:
        # NotADirectoryError doesn't show the full message when printed, so we need to handle it specially
        # Try to determine which path caused the error
        error_path = None
        error_msg = str(e)
        
        # Check if the error message contains a path that matches either of our arguments
        for path in [args.video_dir, args.depth_dir]:
            if str(path) in error_msg:
                error_path = path
                break
        
        # If we couldn't determine which path, check each one
        if error_path is None:
            for path in [args.video_dir, args.depth_dir]:
                if path.exists():
                    if path.is_file():
                        print(f"Error: Path exists as a file, not a directory: {path}", file=sys.stderr)
                        return 2
                    elif path.is_symlink():
                        print(f"Error: Path exists as a symlink, not a directory: {path}", file=sys.stderr)
                        return 2
                    elif not path.is_dir():
                        print(f"Error: Path exists but is not a directory: {path}", file=sys.stderr)
                        return 2
        
        # If we still don't know, print the original error
        if error_path is None:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        
        # We found the problematic path
        if error_path.exists():
            if error_path.is_file():
                print(f"Error: Path exists as a file, not a directory: {error_path}", file=sys.stderr)
            elif error_path.is_symlink():
                print(f"Error: Path exists as a symlink, not a directory: {error_path}", file=sys.stderr)
            else:
                print(f"Error: Path exists but is not a directory: {error_path}", file=sys.stderr)
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())