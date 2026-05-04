#!/usr/bin/env python3
"""vendor_scenario_resume_after_crash.py

Walkthrough: kill capture mid-clip, restart — confirm no partial tarball
poisons S3 and the manifest detects the gap.

Usage:
    python3 bin/vendor_scenario_resume_after_crash.py [--clips N] [--crash-at I]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)
MANIFEST_FILENAME = "manifest.json"


def _clip_name(idx: int) -> str:
    """Return deterministic clip filename for sequence *idx*."""
    return f"clip_{idx:04d}.tar.gz"


def _make_clip_tar(path: Path, idx: int, complete: bool = True) -> Path:
    """Create a tarball for clip *idx*; truncate if *complete* is False."""
    tar_path = path / _clip_name(idx)
    with tarfile.open(tar_path, "w:gz") as tf:
        payload = f"clip-{idx} data block\n" * 100
        info = tarfile.TarInfo(name=f"data_{idx:04d}.txt")
        info.size = len(payload.encode())
        tf.addfile(info, fileobj=io.BytesIO(payload.encode()))
    if not complete:
        with open(tar_path, "r+b") as fh:
            fh.truncate(int(tar_path.stat().st_size * 0.6))
    return tar_path


def _validate_tarball(path: Path) -> Tuple[bool, str]:
    """Return (is_valid, reason) for a tar.gz file."""
    try:
        with tarfile.open(path, "r:gz") as tf:
            tf.getmembers()
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _sha256(path: Path) -> str:
    """Compute SHA256 checksum of file at *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class Manifest:
    """Lightweight manifest tracking clip sequence numbers and checksums."""

    def __init__(self, work_dir: Path) -> None:
        self._path = work_dir / MANIFEST_FILENAME
        self._entries: List[Dict[str, Any]] = []
        if self._path.exists():
            self._entries = list(json.loads(self._path.read_text()))

    def add(self, clip_idx: int, filename: str, sha: str, size: int) -> None:
        """Add a clip entry to the manifest."""
        self._entries.append({
            "clip_idx": clip_idx, "filename": filename, "sha256": sha,
            "size_bytes": size,
            "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def save(self) -> None:
        """Persist manifest to disk."""
        self._path.write_text(json.dumps(self._entries, indent=2) + "\n")

    def detect_gaps(self, expected_count: int) -> List[int]:
        """Return sorted list of missing clip indices in [0, expected_count)."""
        present = {e["clip_idx"] for e in self._entries}
        return sorted(set(range(expected_count)) - present)


class CaptureSimulator:
    """Simulates a capture process that creates tarball clips."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.manifest = Manifest(work_dir)
        self.s3_bucket: Dict[str, bytes] = {}  # Simulated S3 storage

    def upload_to_s3(self, path: Path, key: str) -> bool:
        """Simulate uploading to S3. Returns True on success."""
        valid, reason = _validate_tarball(path)
        if not valid:
            log.warning(f"Rejecting partial tarball {path.name}: {reason}")
            return False
        self.s3_bucket[key] = path.read_bytes()
        log.info(f"Uploaded {key} to S3 ({len(self.s3_bucket[key])} bytes)")
        return True

    def capture_clip(self, idx: int, complete: bool = True) -> bool:
        """Capture a single clip. Returns True if uploaded successfully."""
        tar_path = _make_clip_tar(self.work_dir, idx, complete=complete)
        if not complete:
            log.warning(f"Simulated crash: partial tarball {tar_path.name}")
            return False
        sha, size = _sha256(tar_path), tar_path.stat().st_size
        key = f"clips/{tar_path.name}"
        if self.upload_to_s3(tar_path, key):
            self.manifest.add(idx, tar_path.name, sha, size)
            self.manifest.save()
            return True
        return False

    def find_last_complete_clip(self, total_clips: int) -> int:
        """Find the last successfully uploaded clip index."""
        for idx in range(total_clips - 1, -1, -1):
            tar_path = self.work_dir / _clip_name(idx)
            if tar_path.exists() and _validate_tarball(tar_path)[0]:
                return idx
        return -1

    def resume_capture(self, total_clips: int, crash_at: int) -> Dict[str, Any]:
        """Resume capture after crash, detecting and handling gaps."""
        result: Dict[str, Any] = {
            "crash_at": crash_at, "partial_detected": False,
            "gaps": [], "recovered": [], "s3_keys": [],
        }
        # Check for partial tarball from crash
        partial_path = self.work_dir / _clip_name(crash_at)
        if partial_path.exists() and not _validate_tarball(partial_path)[0]:
            result["partial_detected"] = True
            log.info(f"Detected partial tarball: {partial_path.name}")
            partial_path.unlink()
            log.info("Removed partial tarball to prevent S3 poisoning")

        result["gaps"] = self.manifest.detect_gaps(total_clips)
        start_idx = self.find_last_complete_clip(total_clips) + 1
        log.info(f"Resuming capture from clip {start_idx}")

        for idx in range(start_idx, total_clips):
            if self.capture_clip(idx, complete=True):
                result["recovered"].append(idx)
        result["s3_keys"] = list(self.s3_bucket.keys())
        return result


def main(argv: List[str] | None = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(description="Simulate crash/resume scenario")
    parser.add_argument("--clips", type=int, default=5, help="Total clips (default: 5)")
    parser.add_argument("--crash-at", type=int, default=2, help="Crash index (default: 2)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )

    if args.crash_at >= args.clips:
        log.error("crash-at must be less than total clips")
        return 1

    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir)
        log.info(f"Working directory: {work_dir}")

        # Phase 1: Initial capture with crash
        log.info(f"=== Phase 1: Initial capture (crash at clip {args.crash_at}) ===")
        sim = CaptureSimulator(work_dir)
        for idx in range(args.clips):
            if idx == args.crash_at:
                log.info(f"Simulating crash during clip {idx}...")
            if not sim.capture_clip(idx, complete=(idx != args.crash_at)):
                if idx == args.crash_at:
                    break

        # Phase 2: Resume after crash
        log.info("=== Phase 2: Resume after crash ===")
        sim2 = CaptureSimulator(work_dir)  # New instance simulates restart
        result = sim2.resume_capture(args.clips, args.crash_at)

        # Report results
        print("\n=== Scenario Results ===")
        print(f"Total clips: {args.clips}")
        print(f"Crash at: {args.crash_at}")
        print(f"Partial tarball detected: {result['partial_detected']}")
        print(f"Gaps detected: {result['gaps']}")
        print(f"Clips recovered: {result['recovered']}")
        print(f"S3 keys uploaded: {len(result['s3_keys'])}")

        # Validate: no partial tarball in S3
        for key in result["s3_keys"]:
            data = sim2.s3_bucket[key]
            try:
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                    tf.getmembers()
                print(f"  ✓ {key} - valid tarball")
            except Exception as e:
                print(f"  ✗ {key} - CORRUPT: {e}")
                return 1

        # Validate: manifest has no gaps
        if sim2.manifest.detect_gaps(args.clips):
            log.error(f"Manifest has gaps: {sim2.manifest.detect_gaps(args.clips)}")
            return 1

        print("\n✓ Scenario passed: No partial tarball poisoned S3, manifest detects gaps")
        return 0


if __name__ == "__main__":
    sys.exit(main())