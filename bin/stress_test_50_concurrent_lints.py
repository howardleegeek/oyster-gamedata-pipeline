#!/usr/bin/env python3
"""Stress test: spawn 50 concurrent lint processes against same tarball.

Verifies no shared-state corruption when multiple lint processes operate
on the same tarball simultaneously. Each worker extracts into an isolated
temp directory, performs lint checks, and reports results for comparison.

Usage:
    python3 bin/stress_test_50_concurrent_lints.py <tarball> [--workers N]
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)
NUM_WORKERS_DEFAULT = 50


@dataclass
class LintResult:
    """Result from a single lint worker."""
    worker_id: int
    success: bool
    file_count: int = 0
    total_size: int = 0
    checksum: str = ""
    error: Optional[str] = None
    duration_sec: float = 0.0


def _extract_and_lint(tarball_path: str, worker_id: int) -> LintResult:
    """Extract tarball to isolated temp dir and lint (pure Python)."""
    start = time.monotonic()
    work_dir: Optional[str] = None
    try:
        work_dir = tempfile.mkdtemp(prefix=f"lint_w{worker_id}_")
        with tarfile.open(tarball_path, "r:*") as tf:
            tf.extractall(path=work_dir)
        file_count, total_size, hasher = 0, 0, hashlib.sha256()
        for dirpath, _dn, filenames in os.walk(work_dir):
            for fname in sorted(filenames):
                fpath = os.path.join(dirpath, fname)
                if os.path.isfile(fpath):
                    file_count += 1
                    total_size += os.path.getsize(fpath)
                    with open(fpath, "rb") as fh:
                        for chunk in iter(lambda: fh.read(8192), b""):
                            hasher.update(chunk)
        return LintResult(
            worker_id=worker_id, success=True, file_count=file_count,
            total_size=total_size, checksum=hasher.hexdigest(),
            duration_sec=time.monotonic() - start,
        )
    except Exception as exc:
        return LintResult(
            worker_id=worker_id, success=False, error=str(exc),
            duration_sec=time.monotonic() - start,
        )
    finally:
        if work_dir and os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


def _run_subprocess_lint(
    tarball_path: str, worker_id: int, lint_cmd: List[str],
) -> LintResult:
    """Run an external lint command (list form, no shell=True)."""
    start = time.monotonic()
    work_dir: Optional[str] = None
    try:
        work_dir = tempfile.mkdtemp(prefix=f"lint_sub_w{worker_id}_")
        with tarfile.open(tarball_path, "r:*") as tf:
            tf.extractall(path=work_dir)
        proc = subprocess.run(
            lint_cmd + [work_dir], capture_output=True, text=True, timeout=120,
        )
        return LintResult(
            worker_id=worker_id, success=proc.returncode == 0,
            error=proc.stderr.strip() if proc.returncode else None,
            duration_sec=time.monotonic() - start,
        )
    except subprocess.TimeoutExpired:
        return LintResult(
            worker_id=worker_id, success=False, error="timeout 120s",
            duration_sec=time.monotonic() - start,
        )
    except Exception as exc:
        return LintResult(
            worker_id=worker_id, success=False, error=str(exc),
            duration_sec=time.monotonic() - start,
        )
    finally:
        if work_dir and os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point: spawn N concurrent lint workers, verify consistency.

    Args:
        argv: CLI arguments (defaults to sys.argv[1:]).

    Returns:
        0 if all workers consistent, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Stress test: concurrent lint processes on one tarball",
    )
    parser.add_argument("tarball", help="Path to tarball to stress-test")
    parser.add_argument(
        "--workers", type=int, default=NUM_WORKERS_DEFAULT,
        help=f"Number of concurrent workers (default: {NUM_WORKERS_DEFAULT})",
    )
    parser.add_argument(
        "--lint-cmd", nargs="+", default=None,
        help="Optional external lint command (list form)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    tarball_path = os.path.abspath(args.tarball)
    if not os.path.isfile(tarball_path):
        logger.error("Tarball not found: %s", tarball_path)
        return 1
    logger.info("Starting: %d workers against %s", args.workers, tarball_path)
    t0 = time.monotonic()
    results: List[LintResult] = []
    executor_fn = _run_subprocess_lint if args.lint_cmd else _extract_and_lint
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(executor_fn, tarball_path, wid): wid
            for wid in range(args.workers)
        }
        for future in as_completed(futures):
            wid = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = LintResult(worker_id=wid, success=False, error=str(exc))
            results.append(result)
            logger.debug(
                "Worker %d: %s (%.2fs)", wid,
                "OK" if result.success else "FAIL", result.duration_sec,
            )
    elapsed = time.monotonic() - t0
    results.sort(key=lambda r: r.worker_id)
    passed = True
    success_count = sum(1 for r in results if r.success)
    fail_count = args.workers - success_count
    if fail_count > 0:
        logger.error("%d / %d workers failed", fail_count, args.workers)
        for r in results:
            if not r.success:
                logger.error("  Worker %d: %s", r.worker_id, r.error)
        passed = False
    successful = [r for r in results if r.success]
    if successful:
        ref = successful[0]
        for r in successful[1:]:
            if r.file_count != ref.file_count:
                logger.error(
                    "File count mismatch: worker %d=%d vs expected %d",
                    r.worker_id, r.file_count, ref.file_count,
                )
                passed = False
            if r.checksum != ref.checksum:
                logger.error(
                    "Checksum mismatch: worker %d vs worker %d",
                    r.worker_id, ref.worker_id,
                )
                passed = False
    logger.info(
        "Complete: %d/%d passed in %.2fs", success_count, args.workers, elapsed,
    )
    if passed:
        logger.info("RESULT: PASS — no shared-state corruption detected")
    else:
        logger.error("RESULT: FAIL — inconsistencies or errors found")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
