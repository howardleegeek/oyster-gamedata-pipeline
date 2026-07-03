#!/usr/bin/env python3
"""
Autoresearch Compression Ratio Analyzer.

Compare H.264 vs H.265 vs AV1 sizes on same scene — recommend codec.
"""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CODEC_SETTINGS = {
    "h264": ("libx264", "medium", "23"),
    "h265": ("libx265", "medium", "28"),
    "av1": ("libaom-av1", "4", "30"),
}


def check_ffmpeg() -> bool:
    """Check if ffmpeg is available in PATH."""
    try:
        return subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=10
        ).returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def get_video_info(video_path: Path) -> Optional[Dict]:
    """Get video metadata using ffprobe."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_format", "-show_streams", str(video_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to get video info for %s: %s", video_path, e)
    return None


def encode_video(input_path: Path, output_path: Path, codec: str,
                  timeout: int = 300) -> Tuple[bool, int, float, Optional[str]]:
    """Encode video with specified codec. Returns (success, size, time, error)."""
    if codec not in CODEC_SETTINGS:
        return False, 0, 0.0, f"Unknown codec: {codec}"

    encoder, preset, crf = CODEC_SETTINGS[codec]
    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-c:v", encoder,
           "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", str(output_path)]

    try:
        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        elapsed = time.time() - start
        if result.returncode == 0 and output_path.exists():
            return True, output_path.stat().st_size, elapsed, None
        return False, 0, elapsed, result.stderr[-500:] if result.stderr else "Unknown error"
    except subprocess.TimeoutExpired:
        return False, 0, float(timeout), f"Timeout after {timeout}s"
    except OSError as e:
        return False, 0, 0.0, str(e)


def analyze_results(input_size: int,
                    results: Dict[str, Tuple[bool, int, float, Optional[str]]]) -> List[str]:
    """Analyze compression results and generate recommendations."""
    recs = []
    successful = {k: (s, t) for k, (ok, s, t, e) in results.items() if ok and s > 0}

    if not successful:
        return ["ERROR: No successful encodings. Check ffmpeg and codec support."]

    ratios = {name: input_size / size for name, (size, _) in successful.items()}
    best = max(ratios.items(), key=lambda x: x[1])
    best_size = successful[best[0]][0]

    recs.append(f"RECOMMENDED: {best[0].upper()} (compression: {best[1]:.2f}x)")
    recs.append("\nSize comparison:")
    for name in sorted(successful.keys()):
        size, enc_time = successful[name]
        diff = ((size - best_size) / best_size * 100) if name != best[0] else 0
        recs.append(f"  {name.upper():6s}: {size/1024/1024:7.2f} MB "
                    f"({ratios[name]:.2f}x) {diff:+.1f}% vs best, {enc_time:.1f}s")

    recs.extend([
        "\nCodec characteristics:",
        "  - H.264: Widest compatibility, fast encoding",
        "  - H.265: ~50% smaller than H.264, moderate speed",
        "  - AV1: Best compression, royalty-free, slow encoding"
    ])
    return recs


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description="Compare H.264 vs H.265 vs AV1 compression ratios"
    )
    parser.add_argument("input", type=Path, help="Input video file")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON report path")
    parser.add_argument("--timeout", "-t", type=int, default=300,
                        help="Encoding timeout per codec (default: 300s)")
    parser.add_argument("--codecs", "-c", nargs="+", choices=["h264", "h265", "av1"],
                        default=["h264", "h265", "av1"], help="Codecs to test")

    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        return 1

    if not check_ffmpeg():
        print("ERROR: ffmpeg not found. Please install ffmpeg.", file=sys.stderr)
        return 1

    input_size = args.input.stat().st_size
    print(f"Input: {args.input} ({input_size / 1024 / 1024:.2f} MB)")

    info = get_video_info(args.input)
    if info and "format" in info:
        print(f"Duration: {float(info['format'].get('duration', 0)):.1f}s")

    results: Dict[str, Tuple[bool, int, float, Optional[str]]] = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        for codec in args.codecs:
            print(f"\nEncoding {codec.upper()}...", end=" ", flush=True)
            output_path = tmpdir_path / f"output_{codec}.mp4"
            success, size, enc_time, error = encode_video(
                args.input, output_path, codec, args.timeout
            )
            results[codec] = (success, size, enc_time, error)
            if success:
                print(f"Done ({size / 1024 / 1024:.2f} MB, {enc_time:.1f}s)")
            else:
                print(f"FAILED: {error}")

    print("\n" + "=" * 60)
    for line in analyze_results(input_size, results):
        print(line)

    if args.output:
        report = {
            "input": str(args.input), "input_size_bytes": input_size,
            "results": {k: {"success": s, "size_bytes": sz, "time_seconds": t, "error": e}
                         for k, (s, sz, t, e) in results.items()}
        }
        args.output.write_text(json.dumps(report, indent=2))
        print(f"\nReport saved to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
