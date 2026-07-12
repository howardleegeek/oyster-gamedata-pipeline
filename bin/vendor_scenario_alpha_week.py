#!/usr/bin/env python3
"""
G067 · bin/vendor_scenario_alpha_week.py

Walkthrough: alpha-week first 50 vendors — concurrent ingest + per-vendor quota holds.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_VENDOR_COUNT = 50
DEFAULT_WORKERS = 4
DEFAULT_QUOTA = 1000


@dataclass
class IngestResult:
    """Result of a vendor ingestion operation."""
    vendor_id: str
    success: bool
    records_processed: int
    quota_remaining: int
    error_message: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class QuotaManager:
    """Thread-safe quota manager for vendor resource allocation."""

    def __init__(self) -> None:
        self._locks: Dict[str, threading.Lock] = {}
        self._quotas: Dict[str, int] = {}
        self._global_lock = threading.Lock()

    def register(self, vendor_id: str, quota: int) -> None:
        """Register a vendor with a quota limit."""
        with self._global_lock:
            self._locks[vendor_id] = threading.Lock()
            self._quotas[vendor_id] = quota

    def acquire(self, vendor_id: str, amount: int) -> bool:
        """Attempt to acquire quota. Returns True if successful."""
        with self._global_lock:
            if vendor_id not in self._locks:
                return False
            lock = self._locks[vendor_id]
        with lock:
            if self._quotas.get(vendor_id, 0) >= amount:
                self._quotas[vendor_id] -= amount
                return True
            return False

    def remaining(self, vendor_id: str) -> int:
        """Get remaining quota for a vendor."""
        with self._global_lock:
            return self._quotas.get(vendor_id, 0)


def ingest_vendor(vendor_id: str, quota_mgr: QuotaManager, work_dir: Path) -> IngestResult:
    """Simulate ingestion of vendor data with quota management."""
    records = min(100, quota_mgr.remaining(vendor_id))
    if records == 0:
        return IngestResult(vendor_id, False, 0, 0, "Quota exhausted")

    if not quota_mgr.acquire(vendor_id, records):
        return IngestResult(vendor_id, False, 0, quota_mgr.remaining(vendor_id), "Acquire failed")

    temp_file = work_dir / f"{vendor_id}.json"
    try:
        with open(temp_file, "w") as f:
            json.dump({"vendor_id": vendor_id, "records": records}, f)
    except OSError as e:
        logger.error(f"Write failed for {vendor_id}: {e}")

    remaining = quota_mgr.remaining(vendor_id)
    logger.info(f"Ingested {vendor_id}: {records} records, {remaining} quota left")
    return IngestResult(vendor_id, True, records, remaining)


def run_scenario(vendor_count: int, workers: int, quota: int) -> List[IngestResult]:
    """Execute the alpha-week vendor scenario."""
    logger.info(f"Starting: {vendor_count} vendors, {workers} workers, {quota} quota/vendor")
    quota_mgr = QuotaManager()
    vendor_ids = [f"VND-{i:04d}" for i in range(vendor_count)]
    for vid in vendor_ids:
        quota_mgr.register(vid, quota)

    work_dir = Path(tempfile.mkdtemp(prefix="vendor_scenario_"))
    results: List[IngestResult] = []

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(ingest_vendor, vid, quota_mgr, work_dir): vid for vid in vendor_ids
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    vid = futures[future]
                    results.append(IngestResult(vid, False, 0, 0, str(e)))
    finally:
        for item in work_dir.iterdir():
            item.unlink(missing_ok=True)
        work_dir.rmdir()

    return results


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(description="Alpha-week vendor scenario")
    parser.add_argument("--vendors", "-n", type=int, default=DEFAULT_VENDOR_COUNT)
    parser.add_argument("--workers", "-w", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--quota", "-q", type=int, default=DEFAULT_QUOTA)
    parser.add_argument("--output", "-o", type=str, default=None)
    args = parser.parse_args(argv)

    if args.vendors < 1 or args.workers < 1 or args.quota < 1:
        logger.error("Invalid arguments: vendors, workers, and quota must be >= 1")
        return 1

    results = run_scenario(args.vendors, args.workers, args.quota)
    successful = sum(1 for r in results if r.success)
    total_records = sum(r.records_processed for r in results)
    logger.info(f"Complete: {successful}/{len(results)} succeeded, {total_records} records")

    if args.output:
        try:
            with open(Path(args.output), "w") as f:
                json.dump([r.__dict__ for r in results], f, indent=2)
        except OSError as e:
            logger.error(f"Output write failed: {e}")
            return 1

    return 0 if successful == len(results) else 1


if __name__ == "__main__":
    exit(main())
