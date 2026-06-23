#!/usr/bin/env python3
"""Red-team lint: reject videos tagged 60fps when PRD mandates exactly 30fps.

Usage:
    python3 bin/red_team_wrong_fps.py video1.mp4 video2.mov ...
    python3 bin/red_team_wrong_fps.py --manifest assets.json

Exit codes: 0 = all compliant, 1 = violations found, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)
REQUIRED_FPS: float = 30.0
FPS_TOLERANCE: float = 0.05


def _find_ffprobe() -> Optional[str]:
    """Locate ``ffprobe`` on ``PATH``."""
    return shutil.which("ffprobe")


def _probe_fps(path: str) -> Optional[float]:
    """Return average frame-rate of *path* via ``ffprobe``, or ``None``."""
    ffprobe = _find_ffprobe()
    if ffprobe is None:
        logger.warning("ffprobe not found; skipping %s", path)
        return None
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=r_frame_rate", "-of", "json", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.error("ffprobe failed for %s: %s", path, exc)
        return None
    try:
        data = json.loads(result.stdout)
        raw = data.get("streams", [{}])[0].get("r_frame_rate", "")
        if "/" in raw:
            num, den = raw.split("/", 1)
            return float(num) / float(den)
        return float(raw)
    except (json.JSONDecodeError, ValueError, ZeroDivisionError, IndexError) as exc:
        logger.error("Parse error for %s: %s", path, exc)
        return None


def _check_videos(paths: Iterable[str]) -> Sequence[Tuple[str, str, Optional[float]]]:
    """Probe each video; return list of ``(path, status, fps)`` tuples."""
    results: list[Tuple[str, str, Optional[float]]] = []
    for p in paths:
        if not os.path.isfile(p):
            results.append((p, "MISSING", None))
            continue
        fps = _probe_fps(p)
        if fps is None:
            results.append((p, "UNKNOWN", None))
        elif abs(fps - REQUIRED_FPS) <= FPS_TOLERANCE:
            results.append((p, "OK", fps))
        else:
            results.append((p, "REJECT", fps))
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse CLI args, probe videos, report violations."""
    parser = argparse.ArgumentParser(
        description="Red-team lint: flag videos whose FPS != 30 (PRD requirement).")
    parser.add_argument("videos", nargs="*", metavar="VIDEO", help="Video files to check.")
    parser.add_argument("--manifest", type=str, default=None,
                        help="JSON manifest listing video paths.")
    parser.add_argument("--json-out", type=str, default=None,
                        help="Write structured results to this JSON file.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")

    video_list: list[str] = list(args.videos)
    if args.manifest:
        mp = Path(args.manifest)
        if not mp.is_file():
            logger.error("Manifest not found: %s", args.manifest)
            return 2
        with open(mp) as fh:
            content = fh.read().strip()
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                video_list.extend(str(v) for v in parsed)
            elif isinstance(parsed, dict):
                video_list.extend(str(v) for v in parsed.get("videos", []))
        except json.JSONDecodeError:
            video_list.extend(line.strip() for line in content.splitlines() if line.strip())

    if not video_list:
        parser.print_usage(sys.stderr)
        logger.error("No video files provided.")
        return 2

    results = _check_videos(video_list)
    violations = [r for r in results if r[1] == "REJECT"]
    for path, status, fps in results:
        if status == "OK":
            logger.info("[PASS] %s  (%.2f fps)", path, fps or 0)
        elif status == "REJECT":
            logger.error("[FAIL] %s  (%.2f fps — expected %.2f)", path, fps or 0, REQUIRED_FPS)
        elif status == "MISSING":
            logger.error("[MISS] %s  (file not found)", path)
        else:
            logger.warning("[????] %s  (could not determine FPS)", path)

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump([{"path": p, "status": s, "fps": f} for p, s, f in results], fh, indent=2)
        logger.info("Results written to %s", args.json_out)

    if violations:
        logger.error("RED-TEAM: %d video(s) violate the 30 fps PRD requirement.", len(violations))
        return 1
    logger.info("All probed videos comply with the 30 fps requirement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
