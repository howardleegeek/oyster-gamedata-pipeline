#!/usr/bin/env python3
"""Extract video metadata using ffprobe."""

import argparse
import json
import subprocess
import sys


def extract_metadata(video_path: str) -> dict:
    """Run ffprobe on *video_path* and return a dict with key metadata."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-show_streams",
        "-of", "json",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    # Find the first video stream
    video_stream = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if video_stream is None:
        raise ValueError(f"No video stream found in {video_path}")

    # Duration: prefer stream-level, fall back to format-level
    duration = float(video_stream.get("duration", 0))
    if duration == 0:
        duration = float(data.get("format", {}).get("duration", 0))

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

    # FPS from r_frame_rate (e.g. "30/1")
    fps_str = video_stream.get("r_frame_rate", "0/1")
    num, den = fps_str.split("/")
    fps = round(int(num) / int(den), 2) if int(den) != 0 else 0.0

    codec = video_stream.get("codec_name", "unknown")

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "codec": codec,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract video metadata via ffprobe")
    parser.add_argument("--video", required=True, help="Path to the video file")
    args = parser.parse_args()

    try:
        meta = extract_metadata(args.video)
        print(json.dumps(meta, indent=2))
    except subprocess.CalledProcessError as exc:
        print(f"ffprobe error: {exc.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
