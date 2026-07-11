#!/usr/bin/env python3
"""
vendor_scenario_first_clip.py — Walkthrough: vendor follows README from zero,
uploads first clip, and measures the time-to-first-clip metric.

Usage:
    python3 bin/vendor_scenario_first_clip.py [--workspace DIR] [--dry-run]

Simulates the end-to-end flow a new vendor follows:
  1. Initialise workspace (directories, config skeleton).
  2. Prepare a sample clip (placeholder media file).
  3. Validate the clip (extension, size, non-empty).
  4. "Upload" the clip (local copy or dry-run log).
  5. Report elapsed time as the time-to-first-clip metric.

No external runtime deps beyond stdlib.  PIL is lazily imported.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE: str = "vendor_workspace"
SUPPORTED_EXTENSIONS: List[str] = [".mp4", ".mov", ".avi", ".mkv", ".webm"]
MAX_CLIP_SIZE_MB: int = 500


def _setup_logging(verbose: bool) -> None:
    """Configure root logger with appropriate level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )


def _ensure_workspace(workspace: Path) -> None:
    """Create the vendor workspace directory tree."""
    subdirs = [
        workspace / "clips",
        workspace / "metadata",
        workspace / "logs",
        workspace / "config",
    ]
    for d in subdirs:
        d.mkdir(parents=True, exist_ok=True)
    logger.info("Workspace initialised at %s", workspace)


def _write_default_config(workspace: Path) -> Path:
    """Write a skeleton vendor config file."""
    config_path = workspace / "config" / "vendor.yaml"
    if not config_path.exists():
        config_path.write_text(
            "vendor_id: \"\"\nupload_endpoint: \"\"\nmax_retries: 3\ntimeout_seconds: 60\n"
        )
    logger.info("Default config written to %s", config_path)
    return config_path


def _create_sample_clip(workspace: Path, clip_name: str) -> Path:
    """Create a minimal placeholder clip file for the walkthrough."""
    clip_path = workspace / "clips" / clip_name
    if not clip_path.exists():
        clip_path.write_bytes(b"\x00\x00\x00\x1cftypisom" + b"\x00" * 1024)
    logger.info("Sample clip created at %s (%d bytes)", clip_path, clip_path.stat().st_size)
    return clip_path


def _validate_clip(clip_path: Path) -> Dict[str, Any]:
    """Run basic quality gates on the clip file."""
    checks: List[str] = []
    errors: List[str] = []

    ext = clip_path.suffix.lower()
    if ext in SUPPORTED_EXTENSIONS:
        checks.append(f"extension={ext}")
    else:
        errors.append(f"unsupported extension '{ext}'")

    size_mb = clip_path.stat().st_size / (1024 * 1024)
    if size_mb <= MAX_CLIP_SIZE_MB:
        checks.append(f"size={size_mb:.2f} MB")
    else:
        errors.append(f"file too large: {size_mb:.2f} MB")

    if clip_path.stat().st_size > 0:
        checks.append("non-empty")
    else:
        errors.append("file is empty")

    # Optional duration via PIL (lazy import, best-effort)
    try:
        from PIL import Image
        with Image.open(clip_path) as img:
            n_frames = getattr(img, "n_frames", 1)
            fps = getattr(img, "fps", 25.0)
            checks.append(f"duration={n_frames / fps:.2f}s (est)")
    except Exception as e:
        # PIL is optional: any failure (import error, missing decoder, corrupt
        # file) means we can't compute duration. Log at debug so operators
        # tailing logs can see the cause, then keep going — this is a
        # best-effort probe and must not block validation.
        logger.debug(
            "PIL duration probe failed for %r; recording duration=skipped: %s",
            clip_path,
            e,
            exc_info=True,
        )
        checks.append("duration=skipped (no decoder)")

    return {"valid": len(errors) == 0, "checks": checks, "errors": errors}


def _simulate_upload(clip_path: Path, workspace: Path, dry_run: bool) -> Dict[str, Any]:
    """Simulate uploading the clip to a remote endpoint."""
    upload_dir = workspace / "uploaded"
    upload_dir.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    if dry_run:
        dest = upload_dir / clip_path.name
        shutil.copy2(clip_path, dest)
        logger.info("[DRY-RUN] Clip copied to %s", dest)
    else:
        logger.info("Upload simulation: %s (%d bytes)", clip_path.name, clip_path.stat().st_size)
    elapsed = time.monotonic() - start
    return {"file": clip_path.name, "size_bytes": clip_path.stat().st_size,
            "upload_time_s": round(elapsed, 4), "dry_run": dry_run}


def _write_metric_report(workspace: Path, report: Dict[str, Any]) -> Path:
    """Persist the time-to-first-clip metric as JSON."""
    report_path = workspace / "metadata" / "first_clip_metric.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    logger.info("Metric report written to %s", report_path)
    return report_path


def main(argv: Optional[List[str]] = None) -> int:
    """
    Run the vendor first-clip walkthrough and report the metric.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Exit code (0 = success, 1 = validation failure).
    """
    parser = argparse.ArgumentParser(
        description="Vendor first-clip walkthrough — measure time-to-first-clip.",
    )
    parser.add_argument("--workspace", type=str, default=DEFAULT_WORKSPACE,
                        help="Root workspace directory (default: %(default)s).")
    parser.add_argument("--clip", type=str, default="first_clip.mp4",
                        help="Clip file name (default: %(default)s).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate upload without remote endpoint.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging.")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    workspace = Path(args.workspace).resolve()
    overall_start = time.monotonic()

    logger.info("Step 1/5: Initialising workspace …")
    _ensure_workspace(workspace)

    logger.info("Step 2/5: Writing default config …")
    _write_default_config(workspace)

    logger.info("Step 3/5: Creating sample clip …")
    clip_path = _create_sample_clip(workspace, args.clip)

    logger.info("Step 4/5: Validating clip …")
    validation = _validate_clip(clip_path)
    for check in validation["checks"]:
        logger.info("  ✓ %s", check)
    for err in validation["errors"]:
        logger.error("  ✗ %s", err)
    if not validation["valid"]:
        logger.error("Clip validation failed — aborting.")
        return 1

    logger.info("Step 5/5: Uploading clip …")
    upload_result = _simulate_upload(clip_path, workspace, args.dry_run)

    total_elapsed = time.monotonic() - overall_start
    report: Dict[str, Any] = {
        "scenario": "first_clip", "workspace": str(workspace), "clip": args.clip,
        "validation": {"passed": validation["valid"], "checks": validation["checks"]},
        "upload": upload_result, "time_to_first_clip_s": round(total_elapsed, 4),
    }
    _write_metric_report(workspace, report)

    logger.info("Time-to-first-clip: %.4f s  (dry_run=%s)", total_elapsed, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
