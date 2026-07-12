#!/usr/bin/env python3
"""G089 · Red Team: Concurrent Writers Tarball Corruption Test.

Two adapter processes attempt to write the same tarball simultaneously.
A POSIX file-lock (fcntl.flock) serialises access so only one writer
holds the lock at a time, preventing data corruption.

Usage:
    python3 bin/red_team_concurrent_writers.py [--workers N] [--files N] [--seed S]
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import multiprocessing
import os
import pathlib
import random
import sys
import tarfile
import tempfile
import time
from typing import List, Optional, Tuple


def _make_payloads(directory: pathlib.Path, n: int, seed: int) -> List[pathlib.Path]:
    """Create *n* random binary files under *directory*."""
    rng = random.Random(seed)
    paths: List[pathlib.Path] = []
    for i in range(n):
        p = directory / f"payload_{i:03d}.bin"
        p.write_bytes(bytes(rng.getrandbits(8) for _ in range(rng.randint(512, 8192))))
        paths.append(p)
    return paths


def _tar_bytes(src: pathlib.Path) -> bytes:
    """Return gzip-compressed tarball bytes for all files under *src*."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                tf.add(p, arcname=p.relative_to(src))
    return buf.getvalue()


def _worker(
    src_s: str,
    dst_s: str,
    lock_s: str,
    wid: int,
    timeout: float,
    gate: multiprocessing.Event,
    out: multiprocessing.Queue,
) -> None:
    """Worker process: acquire exclusive lock, then write tarball."""
    gate.wait()  # synchronise start
    lock_fh = open(lock_s, "w")
    try:
        acquired = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                time.sleep(0.005)
        if not acquired:
            out.put((wid, False, "lock timeout"))
            return
        try:
            data = _tar_bytes(pathlib.Path(src_s))
            with open(dst_s, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            out.put((wid, True, None))
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
    except Exception as exc:
        out.put((wid, False, str(exc)))
    finally:
        lock_fh.close()


def _verify(tar_path: pathlib.Path, originals: List[pathlib.Path]) -> Tuple[bool, Optional[str]]:
    """Check tarball integrity against original source files."""
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            members = {m.name: m for m in tf.getmembers()}
            if len(members) != len(originals):
                return False, f"member count {len(members)} != {len(originals)}"
            for src in originals:
                ex = tf.extractfile(src.name)
                if ex is None:
                    return False, f"cannot extract {src.name}"
                if hashlib.sha256(ex.read()).hexdigest() != hashlib.sha256(src.read_bytes()).hexdigest():
                    return False, f"hash mismatch: {src.name}"
        return True, None
    except Exception as exc:
        return False, f"verify error: {exc}"


def run_concurrent_test(workers: int = 2, files: int = 5, seed: int = 42) -> Tuple[bool, dict]:
    """Spawn *workers* processes racing to write one tarball; verify integrity."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        src_dir = tmpdir / "src"
        src_dir.mkdir()
        originals = _make_payloads(src_dir, files, seed)

        dst = tmpdir / "output.tar.gz"
        lock = tmpdir / "write.lock"
        lock.touch()

        gate = multiprocessing.Event()
        queue: multiprocessing.Queue = multiprocessing.Queue()
        procs = [
            multiprocessing.Process(
                target=_worker,
                args=(str(src_dir), str(dst), str(lock), i, 10.0, gate, queue),
            )
            for i in range(workers)
        ]
        for p in procs:
            p.start()
        time.sleep(0.2)  # let workers reach gate.wait()
        gate.set()  # release all at once
        for p in procs:
            p.join(timeout=30)

        results = []
        while not queue.empty():
            results.append(queue.get())

        successes = sum(1 for _, ok, _ in results if ok)
        info = {"workers": workers, "successes": successes, "results": results}

        valid, err = _verify(dst, originals)
        info["tarball_valid"] = valid
        info["tarball_error"] = err
        return valid and successes >= 1, info


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry-point."""
    ap = argparse.ArgumentParser(description="Red-team: concurrent tarball writers with file locking.")
    ap.add_argument("--workers", type=int, default=2, help="Concurrent writer count (default: 2)")
    ap.add_argument("--files", type=int, default=5, help="Payload files per tarball (default: 5)")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed (default: 42)")
    ap.add_argument("-v", "--verbose", action="store_true", help="Print details")
    args = ap.parse_args(argv)

    ok, info = run_concurrent_test(args.workers, args.files, args.seed)
    if args.verbose:
        print(f"Workers:       {info['workers']}")
        print(f"Successes:     {info['successes']}")
        print(f"Tarball valid: {info['tarball_valid']}")
        for _, _s, e in info["results"]:
            if e:
                print(f"  Error: {e}")
    if ok:
        print("[PASS] G089 concurrent-writers red-team test")
        return 0
    print("[FAIL] G089 concurrent-writers red-team test")
    return 1


if __name__ == "__main__":
    sys.exit(main())
