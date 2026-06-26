#!/usr/bin/env python3
"""
replay_determinism_tester.py — Production hardening tool.

Re-runs a replay command twice with the same seed, captures frame-by-frame
output, and asserts byte-identical results to prove determinism.

Usage:
    python3 bin/replay_determinism_tester.py --seed 42 --replay-cmd "python3 replay.py" \
        --output-dir ./replay_out --frame-pattern "*.png"

Exit codes:
    0 — All frames byte-identical (deterministic)
    1 — Mismatch detected (non-deterministic)
    2 — CLI / runtime error
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Return hex SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_frames(directory: Path, pattern: str) -> List[Path]:
    """Return sorted list of files matching *pattern* inside *directory*."""
    return sorted(directory.glob(pattern))


def _run_replay(
    replay_cmd: List[str],
    seed: int,
    output_dir: Path,
    env_overrides: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """
    Execute the replay command with the given seed and output directory.

    Parameters
    ----------
    replay_cmd : list[str]
        Command to execute (list form — never shell=True).
    seed : int
        Random seed injected via environment variable REPLAY_SEED.
    output_dir : Path
        Directory where replay writes its frames.
    env_overrides : dict, optional
        Additional environment variables to merge.

    Returns
    -------
    subprocess.CompletedProcess
    """
    env = os.environ.copy()
    env["REPLAY_SEED"] = str(seed)
    env["REPLAY_OUTPUT_DIR"] = str(output_dir)
    if env_overrides:
        env.update(env_overrides)

    logger.info("Running replay: %s  (seed=%s, out=%s)", replay_cmd, seed, output_dir)
    return subprocess.run(
        replay_cmd,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# ---------------------------------------------------------------------------
# Core comparison logic
# ---------------------------------------------------------------------------


def compare_frame_pairs(
    run_a_frames: List[Path],
    run_b_frames: List[Path],
) -> Tuple[List[Tuple[Path, Path]], List[str]]:
    """
    Compare two lists of frame files pair-wise.

    Returns
    -------
    matched : list of (path_a, path_b) for identical pairs
    mismatches : list of human-readable mismatch descriptions
    """
    matched: List[Tuple[Path, Path]] = []
    mismatches: List[str] = []

    # Ensure same number of frames
    if len(run_a_frames) != len(run_b_frames):
        mismatches.append(
            f"Frame count mismatch: run_a={len(run_a_frames)}, run_b={len(run_b_frames)}"
        )
        # Still compare common prefix
        common = min(len(run_a_frames), len(run_b_frames))
    else:
        common = len(run_a_frames)

    for i in range(common):
        fa, fb = run_a_frames[i], run_b_frames[i]
        if fa.name != fb.name:
            mismatches.append(f"Frame index {i}: filename mismatch '{fa.name}' vs '{fb.name}'")
            continue

        ha, hb = _sha256_file(fa), _sha256_file(fb)
        if ha != hb:
            size_a, size_b = fa.stat().st_size, fb.stat().st_size
            mismatches.append(
                f"Frame '{fa.name}': hash mismatch "
                f"(a={ha[:12]}… b={hb[:12]}…, size_a={size_a}, size_b={size_b})"
            )
        else:
            matched.append((fa, fb))

    return matched, mismatches


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    CLI entry-point.

    Parameters
    ----------
    argv : sequence of str, optional
        Command-line arguments (defaults to sys.argv[1:]).

    Returns
    -------
    int
        0 on deterministic success, 1 on mismatch, 2 on error.
    """
    parser = argparse.ArgumentParser(
        description="Re-run a replay command twice with the same seed and "
        "assert frame-by-frame byte identity.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help="Random seed for both replay runs.",
    )
    parser.add_argument(
        "--replay-cmd",
        nargs="+",
        required=True,
        metavar="CMD",
        help="Replay command (list form, e.g. python3 replay.py).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Base directory for intermediate outputs (default: auto temp dir).",
    )
    parser.add_argument(
        "--frame-pattern",
        default="*.png",
        help="Glob pattern for frame files (default: '*.png').",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Maximum frames to compare; 0 = all (default: 0).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--env",
        nargs="*",
        default=None,
        metavar="KEY=VALUE",
        help="Extra environment variables for the replay command.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Parse extra env vars
    env_overrides: Optional[dict] = None
    if args.env:
        env_overrides = {}
        for item in args.env:
            if "=" not in item:
                logger.error("Invalid --env entry (need KEY=VALUE): %s", item)
                return 2
            k, v = item.split("=", 1)
            env_overrides[k] = v

    # Prepare output directories
    cleanup_needed = args.output_dir is None
    if args.output_dir:
        base_dir = args.output_dir
        base_dir.mkdir(parents=True, exist_ok=True)
    else:
        base_dir = Path(tempfile.mkdtemp(prefix="replay_det_"))

    run_a_dir = base_dir / "run_a"
    run_b_dir = base_dir / "run_b"
    run_a_dir.mkdir(exist_ok=True)
    run_b_dir.mkdir(exist_ok=True)

    logger.info("Run A output: %s", run_a_dir)
    logger.info("Run B output: %s", run_b_dir)

    try:
        # ---- Run A ----
        logger.info("=== Run A ===")
        result_a = _run_replay(args.replay_cmd, args.seed, run_a_dir, env_overrides)
        if result_a.returncode != 0:
            logger.error(
                "Run A failed (rc=%d):\n%s",
                result_a.returncode,
                result_a.stderr.decode(errors="replace"),
            )
            return 2
        logger.info("Run A completed successfully.")

        # ---- Run B ----
        logger.info("=== Run B ===")
        result_b = _run_replay(args.replay_cmd, args.seed, run_b_dir, env_overrides)
        if result_b.returncode != 0:
            logger.error(
                "Run B failed (rc=%d):\n%s",
                result_b.returncode,
                result_b.stderr.decode(errors="replace"),
            )
            return 2
        logger.info("Run B completed successfully.")

        # ---- Collect frames ----
        frames_a = _collect_frames(run_a_dir, args.frame_pattern)
        frames_b = _collect_frames(run_b_dir, args.frame_pattern)

        if args.max_frames > 0:
            frames_a = frames_a[: args.max_frames]
            frames_b = frames_b[: args.max_frames]

        logger.info(
            "Collected %d frame(s) from run A, %d from run B.", len(frames_a), len(frames_b)
        )

        if not frames_a and not frames_b:
            logger.warning("No frames found matching pattern '%s'.", args.frame_pattern)
            return 2

        # ---- Compare ----
        matched, mismatches = compare_frame_pairs(frames_a, frames_b)

        logger.info("Matched: %d  |  Mismatches: %d", len(matched), len(mismatches))

        if mismatches:
            logger.error("=== DETERMINISM CHECK FAILED ===")
            for msg in mismatches:
                logger.error("  • %s", msg)
            return 1

        logger.info("=== DETERMINISM CHECK PASSED — all frames byte-identical ===")
        return 0

    finally:
        if cleanup_needed and base_dir.exists():
            logger.debug("Cleaning up temp directory: %s", base_dir)
            shutil.rmtree(base_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
