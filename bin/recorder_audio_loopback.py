#!/usr/bin/env python3
"""
bin/recorder_audio_loopback.py

Switch ffmpeg dshow input from default microphone to WASAPI loopback
(system audio only, no mic). Falls back to dshow if WASAPI unavailable.
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

WASAPI_LOOPBACK_DEVICE = "audio=loopback"
DSHOW_MICROPHONE_DEVICE = "audio=麦克风"


def check_wasapi_available() -> bool:
    """Check if WASAPI loopback is available on the system."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stderr.lower()
        return "loopback" in output or "wasapi" in output
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def build_ffmpeg_command(
    output_path: Path,
    duration: Optional[int] = None,
    use_wasapi: bool = True,
) -> List[str]:
    """Build ffmpeg command for audio recording."""
    cmd = ["ffmpeg", "-y"]

    if use_wasapi:
        cmd.extend(["-f", "dshow", "-i", WASAPI_LOOPBACK_DEVICE])
    else:
        cmd.extend(["-f", "dshow", "-i", DSHOW_MICROPHONE_DEVICE])

    if duration is not None:
        cmd.extend(["-t", str(duration)])

    cmd.extend(["-acodec", "libmp3lame", "-ab", "192k", str(output_path)])
    return cmd


def record_audio(
    output_path: Path,
    duration: Optional[int] = None,
    prefer_wasapi: bool = True,
) -> int:
    """Record audio using ffmpeg with WASAPI loopback or dshow fallback."""
    use_wasapi = prefer_wasapi and check_wasapi_available()

    if use_wasapi:
        logger.info("Using WASAPI loopback for system audio capture")
    else:
        logger.info("Falling back to dshow microphone input")

    cmd = build_ffmpeg_command(output_path, duration, use_wasapi)
    logger.debug("Running command: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except subprocess.SubprocessError as e:
        logger.error("Failed to run ffmpeg: %s", e)
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for audio loopback recorder."""
    parser = argparse.ArgumentParser(
        description="Record system audio using WASAPI loopback with dshow fallback."
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=Path("recording.mp3"),
        help="Output audio file path (default: recording.mp3)",
    )
    parser.add_argument(
        "--duration", "-d", type=int, default=None,
        help="Recording duration in seconds (default: unlimited)",
    )
    parser.add_argument(
        "--no-wasapi", action="store_true",
        help="Disable WASAPI loopback, use dshow microphone directly",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)

    return record_audio(
        output_path=args.output,
        duration=args.duration,
        prefer_wasapi=not args.no_wasapi,
    )


if __name__ == "__main__":
    sys.exit(main())