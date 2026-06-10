#!/usr/bin/env python3
"""Video Quality Gate — ffprobe-based audit for buyer-facing video quality.

Usage:
    python3 bin/video_quality_gate.py <session_dir> [--json]

Checks per .mp4 file (v0.4.1 spec aligned with buyer PDF):
  1. codec:      h264 OR hevc/h265 (buyer doesn't mandate hevc)
  2. resolution: 1920x1080 required
  3. framerate:  30 fps ±1 required (buyer PDF spec)
  4. bitrate:    ≥ 6 Mbps average required (buyer PDF range 6-12 Mbps)
  5. duration:   300-360 s required (buyer PDF: 5-6 min stable)
  6. pixfmt:     yuv420p required

Exit codes:
  0 — always (SKIP is not an error)
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Requirements (tunable) — aligned with buyer PDF spec (Howard PM review
# 2026-05-18 23:40 PT). v0.4.0 had wrong defaults (HEVC-only / 60fps / ≥60s)
# which would false-FAIL real 300s 30fps sessions. v0.4.1 fixes:
#   codec: accept BOTH h264 and hevc (buyer doesn't mandate hevc)
#   fps:   30 ± 1 (NOT 60 — buyer PDF says 30)
#   dur:   300-360s (NOT ≥60 — buyer PDF says 5-6 min stable)
#   bitrate: ≥ 6 Mbps (real recorder output range is 6-12 Mbps)
# ---------------------------------------------------------------------------
REQ_CODEC = {"h264", "hevc", "h265"}  # h264 OR hevc both acceptable
REQ_WIDTH = 1920
REQ_HEIGHT = 1080
REQ_FPS = 30.0  # buyer PDF: 30fps not 60
REQ_FPS_TOLERANCE = 1.0
REQ_BITRATE_MBPS = 6.0  # buyer PDF range 6-12 Mbps, gate floor at 6
REQ_DURATION_MIN_S = 300.0  # buyer PDF: 5-6 min
REQ_DURATION_MAX_S = 360.0
REQ_PIXFMT = "yuv420p"

# Legacy alias for backward-compat with anything that imported REQ_DURATION_S
REQ_DURATION_S = REQ_DURATION_MIN_S


def _find_mp4(session_dir: str) -> list[str]:
    """Glob *.mp4 in session_dir (non-recursive)."""
    pattern = os.path.join(session_dir, "*.mp4")
    return sorted(glob.glob(pattern))


def _run_ffprobe(filepath: str) -> dict | None:
    """Run ffprobe and return parsed JSON dict, or None on failure."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _parse_fps(r_frame_rate: str) -> float:
    """Parse ffprobe r_frame_rate like '60/1' or '30000/1001' into float."""
    if "/" in r_frame_rate:
        num, den = r_frame_rate.split("/", 1)
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(r_frame_rate)
    except ValueError:
        return 0.0


def _audit_file(filepath: str) -> dict:
    """Audit a single mp4 file. Returns result dict."""
    probe = _run_ffprobe(filepath)

    if probe is None:
        return {
            "file": os.path.basename(filepath),
            "verdict": "SKIP",
            "reason": "ffprobe not available or failed",
        }

    streams = probe.get("streams", [])
    fmt = probe.get("format", {})

    # Find first video stream
    video_stream = None
    for s in streams:
        if s.get("codec_type") == "video":
            video_stream = s
            break

    if video_stream is None:
        return {
            "file": os.path.basename(filepath),
            "verdict": "FAIL",
            "reason": "no video stream found",
        }

    codec_name = video_stream.get("codec_name", "").lower()
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    fps = _parse_fps(video_stream.get("r_frame_rate", "0/1"))
    pix_fmt = video_stream.get("pix_fmt", "")

    duration_s = float(fmt.get("duration", 0))
    bit_rate_bps = float(fmt.get("bit_rate", 0))
    bitrate_mbps = bit_rate_bps / 1e6

    # --- Checks ---
    checks = {}

    # Codec
    checks["codec"] = "PASS" if codec_name in REQ_CODEC else "FAIL"

    # Resolution
    checks["resolution"] = "PASS" if width == REQ_WIDTH and height == REQ_HEIGHT else "FAIL"

    # Framerate
    checks["framerate"] = "PASS" if abs(fps - REQ_FPS) <= REQ_FPS_TOLERANCE else "FAIL"

    # Bitrate
    checks["bitrate"] = "PASS" if bitrate_mbps >= REQ_BITRATE_MBPS else "FAIL"

    # Duration — buyer PDF spec: 5-6 min stable, so RANGE check not just floor
    checks["duration"] = (
        "PASS" if REQ_DURATION_MIN_S <= duration_s <= REQ_DURATION_MAX_S else "FAIL"
    )

    # Pixfmt
    checks["pixfmt"] = "PASS" if pix_fmt == REQ_PIXFMT else "FAIL"

    verdict = "PASS" if all(v == "PASS" for v in checks.values()) else "FAIL"

    return {
        "file": os.path.basename(filepath),
        "codec": codec_name,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "bitrate_mbps": round(bitrate_mbps, 1),
        "duration_s": round(duration_s, 1),
        "pixfmt": pix_fmt,
        "checks": checks,
        "verdict": verdict,
    }


def _human_report(result: dict) -> str:
    """Format result as human-readable text."""
    lines = []
    session_id = os.path.basename(os.path.dirname(result["file"]))
    lines.append(f"VIDEO QUALITY GATE — {session_id}")

    if result.get("verdict") == "SKIP":
        lines.append(f"  File: {result['file']}")
        lines.append(f"  Verdict: SKIP ({result.get('reason', 'unknown')})")
        return "\n".join(lines)

    lines.append(f"  Found 1 mp4 file: {result['file']}")
    lines.append("")

    # Codec
    codec_val = result.get("codec", "?")
    codec_ok = result["checks"]["codec"]
    lines.append(
        f"  Codec:     {codec_val:<12} {'✓ PASS' if codec_ok == 'PASS' else '✗ FAIL'} "
        f"(require: h264 or hevc)"
    )

    # Resolution
    res_val = f"{result.get('width', '?')}x{result.get('height', '?')}"
    res_ok = result["checks"]["resolution"]
    lines.append(
        f"  Resolution: {res_val:<12} {'✓ PASS' if res_ok == 'PASS' else '✗ FAIL'} "
        f"(require: {REQ_WIDTH}x{REQ_HEIGHT})"
    )

    # Framerate
    fps_val = f"{result.get('fps', 0):.3f}"
    fps_ok = result["checks"]["framerate"]
    lines.append(
        f"  Framerate: {fps_val:<12} {'✓ PASS' if fps_ok == 'PASS' else '✗ FAIL'} "
        f"(require: {REQ_FPS}±{REQ_FPS_TOLERANCE})"
    )

    # Bitrate
    br_val = f"{result.get('bitrate_mbps', 0):.1f} Mbps"
    br_ok = result["checks"]["bitrate"]
    lines.append(
        f"  Bitrate:   {br_val:<12} {'✓ PASS' if br_ok == 'PASS' else '✗ FAIL'} "
        f"(require: ≥{REQ_BITRATE_MBPS:.1f} Mbps)"
    )

    # Duration
    dur_val = f"{result.get('duration_s', 0):.1f}s"
    dur_ok = result["checks"]["duration"]
    lines.append(
        f"  Duration:  {dur_val:<12} {'✓ PASS' if dur_ok == 'PASS' else '✗ FAIL'} "
        f"(require: {REQ_DURATION_MIN_S:.0f}-{REQ_DURATION_MAX_S:.0f}s)"
    )

    # Pixfmt
    pix_val = result.get("pixfmt", "?")
    pix_ok = result["checks"]["pixfmt"]
    lines.append(
        f"  Pixfmt:    {pix_val:<12} {'✓ PASS' if pix_ok == 'PASS' else '✗ FAIL'} "
        f"(require: {REQ_PIXFMT})"
    )

    lines.append("")
    lines.append(f"  Verdict: {result['verdict']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Video Quality Gate — ffprobe-based audit")
    parser.add_argument("session_dir", help="Path to session directory")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args(argv)

    session_dir = args.session_dir
    if not os.path.isdir(session_dir):
        print(f"Error: {session_dir} is not a directory", file=sys.stderr)
        return 1

    mp4_files = _find_mp4(session_dir)
    if not mp4_files:
        print(f"No .mp4 files found in {session_dir}", file=sys.stderr)
        return 1

    # Audit first mp4 (spec says single or multiple; we report per-file)
    filepath = mp4_files[0]
    result = _audit_file(filepath)

    if args.json:
        print(json.dumps(result))
    else:
        print(_human_report(result))

    return 0


if __name__ == "__main__":
    sys.exit(main())
