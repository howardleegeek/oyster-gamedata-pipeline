#!/usr/bin/env python3
"""defense_fps_strict.py — Blue-team defense for G096.

Wraps ``ffprobe`` to assert every video stream has ``r_frame_rate``
exactly ``30/1`` (30 fps).  Exit 0 = pass, non-zero = fail.

Usage:
    python defense_fps_strict.py video.mp4 [--strict] [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def _probe_fps(path: Path) -> list[dict]:
    """Return per-stream probe info via ffprobe (list-form call)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=index,r_frame_rate,codec_name",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed (rc={result.returncode}): {result.stderr.strip()}")
    return json.loads(result.stdout).get("streams", [])


def _parse_fps(fraction: str) -> float:
    """Convert ffprobe fraction string (e.g. ``'30/1'``) to float."""
    if "/" in fraction:
        num, den = fraction.split("/", 1)
        den_val = float(den)
        if den_val == 0:
            raise ValueError(f"zero denominator in r_frame_rate: {fraction!r}")
        return float(num) / den_val
    return float(fraction)


def check_fps(path: Path, *, strict: bool = False) -> list[str]:
    """Validate every video stream has r_frame_rate == 30/1.

    When *strict* is True, also reject streams whose avg_frame_rate differs.
    Returns an empty list on success, otherwise a list of error messages.
    """
    streams = _probe_fps(path)
    if not streams:
        return [f"No video streams found in {path.name}"]
    errors: list[str] = []
    for s in streams:
        idx, codec = s.get("index", "?"), s.get("codec_name", "?")
        rfr = s.get("r_frame_rate", "")
        try:
            fps = _parse_fps(rfr)
        except ValueError as exc:
            errors.append(f"stream {idx} ({codec}): {exc}")
            continue
        if fps != 30.0:
            errors.append(
                f"stream {idx} ({codec}): r_frame_rate={rfr} (expected 30/1, got {fps:.4f})"
            )
        if strict:
            afr = s.get("avg_frame_rate", "")
            if afr:
                avg_fps = _parse_fps(afr)
                if avg_fps != 30.0:
                    errors.append(
                        f"stream {idx} ({codec}): avg_frame_rate={afr} (expected 30/1, got {avg_fps:.4f})"
                    )
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry-point.  Returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(
        description="Assert r_frame_rate == 30/1 for all video streams."
    )
    parser.add_argument("files", nargs="+", type=Path, help="Media file(s) to check")
    parser.add_argument("--strict", action="store_true", help="Also verify avg_frame_rate == 30/1")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON")
    args = parser.parse_args(argv)

    all_ok, results = True, {}
    for fpath in args.files:
        if not fpath.is_file():
            results[str(fpath)] = [f"File not found: {fpath}"]
            all_ok = False
            continue
        errs = check_fps(fpath, strict=args.strict)
        results[str(fpath)] = errs
        if errs:
            all_ok = False

    if args.as_json:
        print(json.dumps(results, indent=2))
    else:
        for fname, errs in results.items():
            print(f"[{'PASS' if not errs else 'FAIL'}] {fname}")
            for e in errs:
                print(f"  - {e}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
