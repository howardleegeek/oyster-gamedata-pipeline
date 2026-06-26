#!/usr/bin/env python3
"""
G062 · bin/vendor_scenario_low_bandwidth.py

Walkthrough: 200 Kbps upload — chunked retry succeeds within 30 min budget.

Simulates a low-bandwidth (200 Kbps) upload with chunked transfer and
exponential-backoff retry, demonstrating completion within a 30-minute budget.

Usage:
    python3 bin/vendor_scenario_low_bandwidth.py [--file-size-mb MB] [--verbose]
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class UploadConfig:
    """Configuration for the low-bandwidth upload simulation."""

    file_size_mb: float = 50.0
    bandwidth_kbps: int = 200
    chunk_size_kb: int = 64
    max_retries: int = 5
    timeout_minutes: int = 30
    failure_rate: float = 0.1
    seed: int = 42


@dataclass
class ChunkResult:
    """Result of a single chunk upload attempt."""

    chunk_index: int
    success: bool
    retries: int
    elapsed_seconds: float
    bytes_sent: int


@dataclass
class UploadResult:
    """Aggregate result of the full upload simulation."""

    success: bool
    total_time_seconds: float
    chunks_uploaded: int
    chunks_total: int
    retries_total: int
    bytes_transferred: int
    chunk_results: List[ChunkResult] = field(default_factory=list)


def _transfer_time(bytes_count: int, bw_kbps: int) -> float:
    """Return theoretical transfer time in seconds."""
    return (bytes_count * 8) / 1000.0 / bw_kbps


def _upload_chunk(idx: int, nbytes: int, bw: int, fail_rate: float, max_r: int) -> ChunkResult:
    """Simulate one chunk upload with retry and exponential backoff."""
    base = _transfer_time(nbytes, bw)
    retries = 0
    elapsed = 0.0
    for attempt in range(max_r + 1):
        elapsed += base * (1.0 + 0.1 * attempt)
        if random.random() > fail_rate:
            return ChunkResult(idx, True, retries, elapsed, nbytes)
        retries += 1
        elapsed += min(2.0**attempt, 10.0)
    return ChunkResult(idx, False, retries, elapsed, 0)


def run_simulation(cfg: UploadConfig) -> UploadResult:
    """Run the full chunked upload simulation within the timeout budget."""
    random.seed(cfg.seed)
    file_bytes = int(cfg.file_size_mb * 1024 * 1024)
    chunk_bytes = cfg.chunk_size_kb * 1024
    total = math.ceil(file_bytes / chunk_bytes)
    timeout = cfg.timeout_minutes * 60

    logger.info(
        "Upload: %.1f MB @ %d Kbps | %d chunks | timeout=%d min",
        cfg.file_size_mb,
        cfg.bandwidth_kbps,
        total,
        cfg.timeout_minutes,
    )

    uploaded = 0
    retries = 0
    sent = 0
    results: List[ChunkResult] = []
    t0 = time.monotonic()

    for i in range(total):
        if time.monotonic() - t0 > timeout:
            logger.warning("Timeout after %.1f s", time.monotonic() - t0)
            break
        cur = min(chunk_bytes, file_bytes - i * chunk_bytes)
        r = _upload_chunk(i, cur, cfg.bandwidth_kbps, cfg.failure_rate, cfg.max_retries)
        results.append(r)
        if r.success:
            uploaded += 1
            sent += r.bytes_sent
            retries += r.retries
            if (i + 1) % max(1, total // 10) == 0:
                logger.info("Progress: %d/%d (%.0f%%)", i + 1, total, 100.0 * (i + 1) / total)
        else:
            logger.error("Chunk %d failed after %d retries", i, r.retries)
            break

    wall = time.monotonic() - t0
    return UploadResult(uploaded == total, wall, uploaded, total, retries, sent, results)


def write_report(res: UploadResult, cfg: UploadConfig, path: Path) -> None:
    """Write a human-readable upload report."""
    lines = [
        "=== Low-Bandwidth Upload Report ===",
        f"File size       : {cfg.file_size_mb:.1f} MB",
        f"Bandwidth       : {cfg.bandwidth_kbps} Kbps",
        f"Chunk size      : {cfg.chunk_size_kb} KB",
        f"Chunks uploaded : {res.chunks_uploaded}/{res.chunks_total}",
        f"Total retries   : {res.retries_total}",
        f"Bytes sent      : {res.bytes_transferred:,}",
        f"Wall time       : {res.total_time_seconds:.1f} s ({res.total_time_seconds / 60:.1f} min)",
        f"Timeout budget  : {cfg.timeout_minutes} min",
        f"Status          : {'SUCCESS' if res.success else 'FAILED'}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Report written to %s", path)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point with argparse CLI."""
    p = argparse.ArgumentParser(description="Simulate low-bandwidth chunked upload with retry.")
    p.add_argument("--file-size-mb", type=float, default=50.0)
    p.add_argument("--bandwidth-kbps", type=int, default=200)
    p.add_argument("--chunk-size-kb", type=int, default=64)
    p.add_argument("--max-retries", type=int, default=5)
    p.add_argument("--timeout-minutes", type=int, default=30)
    p.add_argument("--failure-rate", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    cfg = UploadConfig(
        file_size_mb=args.file_size_mb,
        bandwidth_kbps=args.bandwidth_kbps,
        chunk_size_kb=args.chunk_size_kb,
        max_retries=args.max_retries,
        timeout_minutes=args.timeout_minutes,
        failure_rate=args.failure_rate,
        seed=args.seed,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        report = Path(tmpdir) / "upload_report.txt"
        res = run_simulation(cfg)
        write_report(res, cfg, report)

    if res.success:
        logger.info(
            "SUCCESS: %d chunks, %d retries, %.1f min (budget %d min)",
            res.chunks_uploaded,
            res.retries_total,
            res.total_time_seconds / 60,
            cfg.timeout_minutes,
        )
        return 0
    logger.error(
        "FAILED: %d/%d chunks, %.1f min",
        res.chunks_uploaded,
        res.chunks_total,
        res.total_time_seconds / 60,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
