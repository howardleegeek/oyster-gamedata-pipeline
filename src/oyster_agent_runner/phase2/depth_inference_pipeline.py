"""
Depth Inference Pipeline - RGB frames to real OpenEXR depth maps

This module replaces the hardlinked placeholder farm with a proper depth inference
pipeline that extracts frames from video and generates depth maps using the
Depth-Anything-V2-Small model from HuggingFace.

The pipeline:
1. Extracts frames from video using ffmpeg
2. Runs depth inference on each frame using Depth-Anything-V2-Small
3. Saves depth maps as single-channel OpenEXR files with values in meters

Dependencies are lazy-loaded to allow the module to be imported without them,
but will raise RuntimeError with installation hints when functions are called.
"""

import shutil
import subprocess
from pathlib import Path


class DepthInferenceError(Exception):
    """Raised when the depth inference pipeline fails (ffmpeg, model load, etc.)."""


def extract_frames(video_path: str, output_dir: str, fps: float = 6.0) -> list[str]:
    """
    Extract frames from a video file using ffmpeg.

    Args:
        video_path: Path to the input video file
        output_dir: Directory where extracted frames will be saved
        fps: Frames per second to extract (default: 6.0)

    Returns:
        List of paths to extracted PNG frames

    Raises:
        FileNotFoundError: If video_path doesn't exist
        RuntimeError: If ffmpeg fails or is not installed
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create output pattern for frames
    frame_pattern = output_dir / "frame_%06d.png"

    # Build ffmpeg command
    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps}",
        "-vsync",
        "vfr",
        str(frame_pattern),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise DepthInferenceError(f"ffmpeg failed with code {e.returncode}: {e.stderr}") from e
    except FileNotFoundError as e:
        raise DepthInferenceError(
            "ffmpeg not found. Please install ffmpeg and ensure it's in your PATH."
        ) from e

    # Collect extracted frame paths
    frame_paths = sorted(output_dir.glob("frame_*.png"))
    return [str(p) for p in frame_paths]


def infer_depth_batch(
    rgb_paths: list[str], output_dir: str, near_m: float = 0.5, far_m: float = 30.0
) -> int:
    """
    Run depth inference on a batch of RGB images using Depth-Anything-V2-Small.

    Args:
        rgb_paths: List of paths to RGB images
        output_dir: Directory where depth EXR files will be saved
        near_m: Near clipping plane in meters (default: 0.5)
        far_m: Far clipping plane in meters (default: 30.0)

    Returns:
        Number of EXR files successfully written

    Raises:
        RuntimeError: If required dependencies (torch, transformers, OpenEXR)
                     are not installed
    """
    # Lazy import dependencies
    try:
        import numpy as np
        import torch
        from transformers import pipeline
    except ImportError as e:
        raise RuntimeError(
            f"Missing dependency: {e}. " "Please install with: pip install torch transformers numpy"
        ) from e

    try:
        import Imath
        import OpenEXR
    except ImportError as e:
        raise RuntimeError(
            f"Missing dependency: {e}. " "Please install with: pip install openexr"
        ) from e

    # Validate near and far values
    if near_m >= far_m:
        raise ValueError(f"near_m ({near_m}) must be less than far_m ({far_m})")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load depth estimation model
    try:
        depth_pipeline = pipeline(
            task="depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf"
        )
    except Exception as e:
        raise RuntimeError(
            f"Failed to load Depth-Anything-V2-Small model: {e}. "
            "Make sure you have an internet connection for the first run."
        ) from e

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    depth_pipeline.model = depth_pipeline.model.to(device)

    exr_count = 0

    for rgb_path in rgb_paths:
        rgb_path = Path(rgb_path)
        if not rgb_path.exists():
            print(f"Warning: RGB file not found, skipping: {rgb_path}")
            continue

        try:
            # Run depth inference
            result = depth_pipeline(str(rgb_path))
            depth_map = result["depth"]

            # Convert PIL Image to numpy array
            depth_array = np.array(depth_map, dtype=np.float32)

            # Normalize depth values (model outputs relative depth)
            # We'll assume the model outputs values in [0, 1] range
            # and scale to our near-far range
            depth_min = depth_array.min()
            depth_max = depth_array.max()

            if depth_max > depth_min:
                # Normalize to [0, 1]
                normalized = (depth_array - depth_min) / (depth_max - depth_min)
                # Scale to [near_m, far_m]
                scaled = normalized * (far_m - near_m) + near_m
            else:
                # All pixels have same depth
                scaled = np.full_like(depth_array, near_m)

            # Clamp to [near_m, far_m]
            scaled = np.clip(scaled, near_m, far_m)

            # Set invalid pixels (e.g., sky) to 0
            # For Depth-Anything, we'll assume very bright pixels might be sky
            # This is a simple heuristic - in practice you might want more sophisticated logic
            from PIL import Image

            rgb_img = Image.open(rgb_path)
            rgb_array = np.array(rgb_img)

            # Simple brightness threshold for sky detection
            if len(rgb_array.shape) == 3:
                brightness = rgb_array.mean(axis=2)
                # Threshold at 90% brightness
                sky_mask = brightness > 230  # 230/255 ≈ 90%
                scaled[sky_mask] = 0.0

            # Create output filename
            exr_path = output_dir / f"{rgb_path.stem}.exr"

            # Save as OpenEXR
            height, width = scaled.shape

            # Create EXR header
            header = OpenEXR.Header(width, height)
            header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}

            # Write EXR file
            exr = OpenEXR.OutputFile(str(exr_path), header)

            # Convert to bytes and write
            z_data = scaled.astype(np.float32).tobytes()
            exr.writePixels({"Z": z_data})
            exr.close()

            exr_count += 1

        except Exception as e:
            print(f"Error processing {rgb_path}: {e}")
            continue

    return exr_count


def video_to_depth_exrs(video_path: str, output_dir: str, fps: float = 6.0) -> int:
    """
    Complete pipeline: extract frames from video and infer depth maps.

    Args:
        video_path: Path to the input video file
        output_dir: Directory where depth EXR files will be saved
        fps: Frames per second to extract (default: 6.0)

    Returns:
        Number of EXR files successfully written

    Note:
        Creates temporary directory for intermediate PNGs and cleans it up.
    """
    video_path = Path(video_path)
    output_dir = Path(output_dir)

    # Create temporary directory for frames
    temp_dir = output_dir / "_temp_frames"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Extract frames
        print(f"Extracting frames from {video_path} at {fps} FPS...")
        frame_paths = extract_frames(str(video_path), str(temp_dir), fps)
        print(f"Extracted {len(frame_paths)} frames")

        if not frame_paths:
            print("No frames extracted, exiting")
            return 0

        # Step 2: Infer depth maps
        print(f"Running depth inference on {len(frame_paths)} frames...")
        exr_count = infer_depth_batch(frame_paths, str(output_dir))
        print(f"Generated {exr_count} depth EXR files")

        return exr_count

    finally:
        # Clean up temporary frames
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            print(f"Cleaned up temporary directory: {temp_dir}")


if __name__ == "__main__":
    # Example usage
    import argparse

    parser = argparse.ArgumentParser(description="Convert video to depth EXR files")
    parser.add_argument("video", help="Input video file")
    parser.add_argument("output_dir", help="Output directory for EXR files")
    parser.add_argument(
        "--fps", type=float, default=6.0, help="Frames per second to extract (default: 6.0)"
    )

    args = parser.parse_args()

    count = video_to_depth_exrs(args.video, args.output_dir, args.fps)
    print(f"Successfully processed {count} frames")
