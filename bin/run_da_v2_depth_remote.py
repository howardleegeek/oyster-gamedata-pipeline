#!/usr/bin/env python3
"""
Drop-in replacement for bin/run_da_v2_depth.py — uses remote Modal endpoint.

Same CLI as local DA-V2:
    python3 bin/run_da_v2_depth_remote.py --frames-dir X --depth-dir Y \
        --endpoint https://oyster-depth.modal.run \
        --auth-token $MODAL_TOKEN

If endpoint is unreachable or inference fails, exits non-zero so callers can
fall back to local ONNX without silently accepting missing depth.
"""

import argparse
import glob
import io
import os
import subprocess
import tarfile
import time

import requests


def parse_args():
    parser = argparse.ArgumentParser(description="Compute DA-V2 depth via remote Modal endpoint")
    parser.add_argument(
        "--frames-dir",
        required=True,
        help="Directory containing input frames (jpg/png)",
    )
    parser.add_argument(
        "--depth-dir",
        required=True,
        help="Output directory for depth EXR files",
    )
    parser.add_argument(
        "--endpoint",
        default="https://oyster-depth.modal.run",
        help="Modal endpoint URL",
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("MODAL_TOKEN", ""),
        help="Modal auth token (or set MODAL_TOKEN env var)",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=6,
        help="Frame rate for video encoding (default: 6)",
    )
    parser.add_argument(
        "--skip-depth",
        action="store_true",
        help="Skip depth computation entirely (graceful fallback)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="HTTP request timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--video-path",
        default=None,
        help="Optional: path to mp4 video (if provided, skip frames-dir tar)",
    )
    return parser.parse_args()


def create_video_from_frames(frames_dir: str, fps: int) -> bytes:
    """
    Create an mp4 video from frames directory using ffmpeg.
    Returns the video bytes.
    """
    import tempfile

    frame_pattern = os.path.join(frames_dir, "frame_%06d.jpg")
    use_glob = False
    start_number = None
    frame_files = glob.glob(os.path.join(frames_dir, "frame_[0-9][0-9][0-9][0-9][0-9][0-9].jpg"))
    if frame_files:
        first_stem = os.path.splitext(os.path.basename(sorted(frame_files)[0]))[0]
        start_number = first_stem.rsplit("_", 1)[-1].lstrip("0") or "0"
    else:
        frame_pattern = os.path.join(frames_dir, "%06d.png")
        frame_files = glob.glob(os.path.join(frames_dir, "[0-9][0-9][0-9][0-9][0-9][0-9].png"))
        if frame_files:
            first_stem = os.path.splitext(os.path.basename(sorted(frame_files)[0]))[0]
            start_number = first_stem.lstrip("0") or "0"
        else:
            frame_pattern = os.path.join(frames_dir, "*.jpg")
            use_glob = True
            if not glob.glob(frame_pattern):
                frame_pattern = os.path.join(frames_dir, "*.png")
                if not glob.glob(frame_pattern):
                    raise RuntimeError(f"no jpg/png frames found in {frames_dir}")

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "input.mp4")

        cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
        ]
        if start_number is not None:
            cmd.extend(["-start_number", start_number])
        if use_glob:
            cmd.extend(["-pattern_type", "glob"])
        cmd.extend(
            [
                "-i",
                frame_pattern,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
                video_path,
            ]
        )

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr}")

        with open(video_path, "rb") as f:
            return f.read()


def upload_and_compute(
    video_bytes: bytes,
    endpoint: str,
    auth_token: str,
    fps: int,
    timeout: int,
) -> bytes:
    """
    POST video to Modal endpoint, receive tar.gz of depth EXRs.
    """
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    # Try multipart form upload first
    files = {"video": ("input.mp4", video_bytes, "video/mp4")}
    data = {"fps": str(fps)}

    url = endpoint.rstrip("/")
    # Try the async endpoint first, fall back to sync
    endpoints_to_try = [
        f"{url}/depth-endpoint-async",
        f"{url}/depth-endpoint",
        url,
    ]

    last_error = None
    for ep in endpoints_to_try:
        try:
            print(f"Trying endpoint: {ep}")
            response = requests.post(
                ep,
                files=files,
                data=data,
                headers=headers,
                timeout=timeout,
                stream=True,
            )

            if response.status_code == 200:
                print(f"Success from {ep}")
                # Read the tar.gz response
                tar_bytes = io.BytesIO()
                for chunk in response.iter_content(chunk_size=8192):
                    tar_bytes.write(chunk)
                tar_bytes.seek(0)
                return tar_bytes.getvalue()
            else:
                print(f"Endpoint {ep} returned {response.status_code}: {response.text[:200]}")
                last_error = f"HTTP {response.status_code}"
        except requests.exceptions.ConnectionError as e:
            print(f"Connection error for {ep}: {e}")
            last_error = str(e)
        except requests.exceptions.Timeout as e:
            print(f"Timeout for {ep}: {e}")
            last_error = str(e)
        except Exception as e:
            print(f"Error for {ep}: {e}")
            last_error = str(e)

    raise RuntimeError(f"All endpoints failed. Last error: {last_error}")


def extract_depth_exrs(tar_bytes: bytes, depth_dir: str):
    """
    Extract depth EXR files from tar.gz into depth_dir.
    """
    os.makedirs(depth_dir, exist_ok=True)

    tar_buffer = io.BytesIO(tar_bytes)
    with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
        # Extract all files, stripping the 'depth/' prefix
        members = tar.getmembers()
        for member in members:
            if member.name.startswith("depth/"):
                # Strip the prefix
                member.name = member.name[len("depth/") :]
                if member.name:  # Skip empty names
                    tar.extract(member, path=depth_dir)

    # Verify extraction
    exr_files = glob.glob(os.path.join(depth_dir, "*.exr"))
    print(f"Extracted {len(exr_files)} EXR files to {depth_dir}")
    return exr_files


def write_source_marker(depth_dir: str, *, backend: str = "modal-a10g") -> None:
    """
    Write depth/.source marker file for H8 audit trail.
    """
    source_path = os.path.join(depth_dir, ".source")
    with open(source_path, "w") as f:
        f.write("kind: monocular_da_v2\n")
        f.write(f"timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        f.write("model: depth-anything/Depth-Anything-V2-Small-hf\n")
        f.write(f"compute: {backend}\n")
        f.write("units: relative\n")
        f.write("view_space_z: false\n")
        f.write("legacy_kind: server_da_v2\n")
        f.write("legacy_model: depth-anything-v2-small\n")
    print(f"Written source marker: {source_path}")


def main() -> int:
    args = parse_args()

    # Graceful fallback: skip depth if requested
    if args.skip_depth:
        print("--skip-depth: skipping depth computation")
        os.makedirs(args.depth_dir, exist_ok=True)
        write_source_marker(args.depth_dir)
        return 0

    # Check endpoint reachability
    print(f"Checking endpoint: {args.endpoint}")
    try:
        response = requests.get(args.endpoint.rstrip("/"), timeout=10)
        print(f"Endpoint reachable (status: {response.status_code})")
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Endpoint unreachable: {e}")
        return 1

    # Create video from frames
    print(f"Creating video from frames in {args.frames_dir}...")
    try:
        if args.video_path:
            with open(args.video_path, "rb") as f:
                video_bytes = f.read()
            print(f"Loaded video from {args.video_path} ({len(video_bytes)} bytes)")
        else:
            video_bytes = create_video_from_frames(args.frames_dir, args.fps)
            print(f"Created video ({len(video_bytes)} bytes)")
    except Exception as e:
        print(f"ERROR: Failed to create video: {e}")
        return 1

    # Upload and compute depth
    print("Uploading to Modal endpoint...")
    try:
        tar_bytes = upload_and_compute(
            video_bytes,
            args.endpoint,
            args.auth_token,
            args.fps,
            args.timeout,
        )
    except Exception as e:
        print(f"ERROR: Depth computation failed: {e}")
        return 1

    # Extract results
    print("Extracting depth EXRs...")
    exr_files = extract_depth_exrs(tar_bytes, args.depth_dir)

    # Write source marker
    write_source_marker(args.depth_dir)

    print(f"Done! {len(exr_files)} depth EXRs in {args.depth_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
