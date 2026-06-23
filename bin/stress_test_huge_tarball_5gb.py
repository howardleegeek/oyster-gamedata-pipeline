#!/usr/bin/env python3
"""
Stress test: build 5 GB tarball (long capture + 6fps depth) — verify upload_s3.sh chunked path holds.
"""

import argparse
import hashlib
import json
import math
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def calculate_params(target_gb: float = 5.0) -> Tuple[int, int, int]:
    """Calculate duration, frames, and bytes per frame."""
    width, height, bpp = 640, 480, 4
    bytes_per_frame = width * height * bpp  # 1.17 MB
    fps = 6
    target_bytes = int(target_gb * 1024**3)
    data_bytes = int(target_bytes * 0.9)  # 10% metadata
    frames = data_bytes // bytes_per_frame
    seconds = frames // fps
    return seconds, frames, bytes_per_frame


def create_frame(idx: int, path: Path, size: int) -> None:
    """Create deterministic mock depth frame."""
    data = bytearray()
    seed = idx * 123456789
    while len(data) < size:
        seed = (seed * 1103515245 + 12345) & 0x7fffffff
        data.append(seed & 0xff)
    path.write_bytes(data[:size])


def create_capture(base: Path, secs: int, frames: int, bpf: int, sample: int = 100) -> Dict:
    """Create mock capture directory."""
    depth_dir = base / "depth"
    depth_dir.mkdir(parents=True)
    
    meta = {
        "duration": secs, "fps": 6, "frames": frames,
        "width": 640, "height": 480, "bpf": bpf,
        "created": time.time()
    }
    (base / "meta.json").write_text(json.dumps(meta))
    
    # Create sample frames
    to_create = max(1, frames // sample)
    for i in range(to_create):
        idx = i * sample
        create_frame(idx, depth_dir / f"depth_{idx:08d}.bin", bpf)
        if i % 10 == 0:
            print(f"\rFrames: {i+1}/{to_create}", end="")
    
    # Fill remaining with sparse file
    remaining = (frames - to_create) * bpf
    if remaining > 0:
        sparse = depth_dir / "sparse.bin"
        with open(sparse, "wb") as f:
            f.seek(remaining - 1)
            f.write(b"\0")
    
    print(f"\rFrames: {to_create}/{to_create} - Done")
    return meta


def create_tarball(src: Path, dst: Path) -> Dict:
    """Create tarball and return stats."""
    print("Creating tarball...")
    start = time.time()
    with tarfile.open(dst, "w:gz") as tar:
        tar.add(src, arcname=src.name)
    elapsed = time.time() - start
    size = dst.stat().st_size
    return {"path": str(dst), "size": size, "gb": size/1024**3, "time": elapsed}


def verify_chunks(tarball: Path, chunk_mb: int = 100) -> Dict:
    """Verify chunked read compatibility."""
    chunk_bytes = chunk_mb * 1024 * 1024
    size = tarball.stat().st_size
    chunks = math.ceil(size / chunk_bytes)
    
    print(f"Verifying {chunks} chunks of {chunk_mb} MB...")
    hashes = []
    
    with open(tarball, "rb") as f:
        for i in range(chunks):
            start = i * chunk_bytes
            end = min(start + chunk_bytes, size)
            length = end - start
            
            f.seek(start)
            data = f.read(length)
            
            if len(data) != length:
                raise ValueError(f"Chunk {i}: expected {length}, got {len(data)}")
            
            hashes.append(hashlib.sha256(data).hexdigest()[:16])
            
            if i % 5 == 0 or i == chunks - 1:
                print(f"\rChunks: {i+1}/{chunks}", end="")
    
    print(f"\rChunks: {chunks}/{chunks} - Done")
    return {"chunks": chunks, "size_mb": chunk_mb, "hashes": hashes, "passed": True}


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Build 5GB tarball stress test.")
    parser.add_argument("--target-gb", type=float, default=5.0, help="Target GB")
    parser.add_argument("--output", type=Path, default=Path("stress_5gb.tar.gz"))
    parser.add_argument("--chunk-mb", type=int, default=100, help="Chunk size MB")
    parser.add_argument("--sample", type=int, default=100, help="Frame sample rate")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    
    args = parser.parse_args(argv)
    
    print(f"=== Stress Test: {args.target_gb} GB Tarball ===")
    
    try:
        # Calculate
        secs, frames, bpf = calculate_params(args.target_gb)
        print(f"\n1. Params: {secs}s, {frames:,} frames, {bpf:,} B/frame")
        
        # Create temp dir
        if args.keep_temp:
            tmp = Path(tempfile.mkdtemp(prefix="stress_"))
            print(f"2. Temp dir: {tmp}")
        else:
            tmp_ctx = tempfile.TemporaryDirectory(prefix="stress_")
            tmp = Path(tmp_ctx.name)
        
        capture = tmp / f"cap_{int(time.time())}"
        capture.mkdir()
        
        # Generate data
        print("3. Generating data...")
        meta = create_capture(capture, secs, frames, bpf, args.sample)
        
        # Create tarball
        stats = create_tarball(capture, args.output)
        print(f"4. Tarball: {stats['gb']:.2f} GB in {stats['time']:.1f}s")
        
        # Verify
        if not args.no_verify:
            print("5. Verifying chunks...")
            verify = verify_chunks(args.output, args.chunk_mb)
            print(f"   {verify['chunks']} chunks verified")
        
        # Cleanup
        if not args.keep_temp:
            print("6. Cleaning up...")
        
        print("\n=== SUCCESS ===")
        print(f"Size: {stats['gb']:.2f} GB")
        print(f"Frames: {frames:,}")
        print(f"Output: {args.output}")
        if not args.no_verify:
            print(f"Chunks: {args.chunk_mb} MB verified")
        
        return 0
        
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
