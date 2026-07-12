#!/usr/bin/env python3
"""
red_team_sigkill_mid_write.py — Red-team: SIGKILL adapter mid action_camera write.

Demonstrates that an atomic temp+rename write pattern leaves no half-file
even when the writer process is SIGKILL'd mid-write.  The script spawns a
child that writes to a temp file then renames; a parent sends SIGKILL at
various points and inspects the output directory for partial artefacts.

Usage:
    python3 bin/red_team_sigkill_mid_write.py [--iterations N] [--delay-ms D]
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

_CHILD_SCRIPT = """\
import os, sys, time, tempfile
from pathlib import Path
import logging
logging.basicConfig(level=logging.WARNING, format='%(message)s')
target_dir, sig_fd = sys.argv[1], int(sys.argv[2])
payload_size, chunk_size, sleep_t = {payload_size}, {chunk_size}, {sleep_per_chunk}
payload = b"\\x00" * payload_size
final_path = Path(target_dir) / "action_camera.dat"
fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".action_camera_", suffix=".tmp")
try:
    with os.fdopen(fd, "wb") as f:
        for i in range(0, len(payload), chunk_size):
            f.write(payload[i:i+chunk_size]); f.flush()
            try: os.write(sig_fd, b"1")
            except OSError as e:
                logging.warning("Child: failed to signal parent: %s", e)
            time.sleep(sleep_t)
    os.rename(tmp_path, final_path)
except OSError as e:
    logging.warning("Child: write/rename failed: %s", e)
"""


def _build_child_script(payload_size: int, chunk_size: int, sleep_per_chunk: float) -> str:
    """Return the child writer script with parameters interpolated."""
    return _CHILD_SCRIPT.format(
        payload_size=payload_size, chunk_size=chunk_size, sleep_per_chunk=sleep_per_chunk)


def _run_sigkill_trial(
    work_dir: Path, payload_size: int, chunk_size: int,
    sleep_per_chunk: float, kill_after_chunks: int,
) -> dict:
    """Spawn a writer, SIGKILL it after *kill_after_chunks* chunks, inspect results."""
    script_path = work_dir / "_writer.py"
    script_path.write_text(_build_child_script(payload_size, chunk_size, sleep_per_chunk))
    r_fd, w_fd = os.pipe()
    proc = subprocess.Popen(
        [sys.executable, str(script_path), str(work_dir), str(w_fd)],
        pass_fds=(w_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w_fd)
    chunks_seen = 0
    try:
        while chunks_seen < kill_after_chunks:
            if not os.read(r_fd, 1):
                break
            chunks_seen += 1
    except OSError as e:
        logging.warning("Parent: failed to read signal from child: %s", e)
    os.close(r_fd)
    time.sleep(0.05)
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)
    tmp_files = list(work_dir.glob(".action_camera_*.tmp"))
    final_files = list(work_dir.glob("action_camera.dat"))
    return {
        "chunks_written": chunks_seen,
        "tmp_files": [str(f) for f in tmp_files],
        "final_files": [str(f) for f in final_files],
        "partial_found": len(tmp_files) > 0,
        "final_found": len(final_files) > 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Entry-point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="Red-team: SIGKILL adapter mid action_camera write")
    parser.add_argument("--iterations", type=int, default=5, help="Number of SIGKILL trials")
    parser.add_argument("--delay-ms", type=float, default=10.0, help="Sleep per chunk (ms)")
    parser.add_argument("--payload-kb", type=int, default=64, help="Total payload size (KB)")
    parser.add_argument("--chunk-kb", type=int, default=4, help="Chunk size per write (KB)")
    args = parser.parse_args(argv)
    payload_size = args.payload_kb * 1024
    chunk_size = args.chunk_kb * 1024
    sleep_per_chunk = args.delay_ms / 1000.0
    with tempfile.TemporaryDirectory(prefix="redteam_sigkill_") as tmpdir:
        work_dir = Path(tmpdir)
        total_chunks = payload_size // chunk_size
        print("[red-team] SIGKILL mid-write test")
        print(f"  payload={args.payload_kb}KB  chunk={args.chunk_kb}KB  "
              f"sleep={args.delay_ms}ms/chunk  iterations={args.iterations}\n")
        partial_count = final_count = 0
        for i in range(1, args.iterations + 1):
            kill_at = (i * 3) % max(total_chunks - 1, 1) + 1
            result = _run_sigkill_trial(
                work_dir, payload_size, chunk_size, sleep_per_chunk, kill_at)
            status = "PARTIAL-FILE" if result["partial_found"] else "CLEAN"
            if result["partial_found"]:
                partial_count += 1
            if result["final_found"]:
                final_count += 1
            print(f"  trial {i:2d}: kill_after={kill_at} chunks  "
                  f"written={result['chunks_written']}  status={status}")
        print()
        if partial_count > 0:
            print(f"[FAIL] {partial_count}/{args.iterations} trials left partial .tmp files")
            print("  -> atomic temp+rename was NOT used by the writer")
            return 1
        else:
            print(f"[PASS] 0/{args.iterations} partial files - atomic temp+rename is safe")
            return 0


if __name__ == "__main__":
    sys.exit(main())
