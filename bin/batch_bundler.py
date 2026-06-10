#!/usr/bin/env python3
"""
batch_bundler.py — Bundle N finalized sessions into a tarball + Merkle manifest.
"""

import argparse
import datetime
import hashlib
import json
import sys
import tarfile
from pathlib import Path
from typing import Dict, List, Tuple


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_merkle_tree(hashes: List[str]) -> str:
    if not hashes:
        return sha256_bytes(b"")
    nodes = [bytes.fromhex(h) for h in hashes]
    n = len(nodes)
    next_power_of_2 = 2  # minimum so single-leaf hashes leaf||zero_hash
    while next_power_of_2 < n:
        next_power_of_2 <<= 1
    zero_hash = sha256_bytes(b"")
    zero_hash_bytes = bytes.fromhex(zero_hash)
    nodes.extend([zero_hash_bytes] * (next_power_of_2 - n))
    while len(nodes) > 1:
        new_level = []
        for i in range(0, len(nodes), 2):
            left = nodes[i]
            right = nodes[i + 1] if i + 1 < len(nodes) else zero_hash_bytes
            parent = hashlib.sha256(left + right).digest()
            new_level.append(parent)
        nodes = new_level
    return nodes[0].hex()


def process_session(session_dir: Path) -> Tuple[str, int, int, List[Dict]]:
    """Returns (session_sha256, file_count, total_bytes, file_list)."""
    file_list = []
    total_bytes = 0
    file_hashes = []
    for p in sorted(session_dir.rglob("*")):
        if p.is_file():
            relpath = str(p.relative_to(session_dir))
            sha = sha256_file(p)
            size = p.stat().st_size
            file_list.append({"path": relpath, "sha256": sha, "bytes": size})
            file_hashes.append(sha)
            total_bytes += size
    # Bug 2 fix: sort file hashes before concatenation for deterministic session_sha256
    file_hashes.sort()
    session_sha256 = sha256_bytes("".join(file_hashes).encode("utf-8"))
    return session_sha256, len(file_list), total_bytes, file_list


def build_manifest(session_results: List[Dict], merkle_root: str) -> Dict:
    """Build the JSON manifest for the bundle."""
    return {
        "version": "1.0",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "merkle_root": merkle_root,
        "sessions": session_results,
    }


def main():
    parser = argparse.ArgumentParser(description="Batch bundler for Oyster sessions.")
    parser.add_argument("sessions", nargs="+", help="Session directories.")
    parser.add_argument("--output-dir", required=True, help="Where to write tarball + manifest.")
    args = parser.parse_args()

    for s in args.sessions:
        if not Path(s).is_dir():
            # Bug 1 fix: print error to stderr, not stdout
            print(f"Error: Session directory does not exist: {s}", file=sys.stderr)
            sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    session_results = []
    all_file_hashes = []

    for session_path in args.sessions:
        session_dir = Path(session_path)
        session_id = session_dir.name

        session_sha256, file_count, total_bytes, file_list = process_session(session_dir)

        session_results.append(
            {
                "session_id": session_id,
                "session_sha256": session_sha256,
                "file_count": file_count,
                "total_bytes": total_bytes,
                "files": file_list,
            }
        )

        # Collect all file hashes for the Merkle tree
        for f in file_list:
            all_file_hashes.append(f["sha256"])

    # Build Merkle tree from all file hashes across all sessions
    merkle_root = build_merkle_tree(all_file_hashes)

    # Build manifest
    manifest = build_manifest(session_results, merkle_root)

    # Write manifest
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Bug 3 fix: use arcname=session_id so subdir/file.txt becomes session_id/subdir/file.txt
    tarball_path = output_dir / "bundle.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        for session_path in args.sessions:
            session_dir = Path(session_path)
            session_id = session_dir.name
            tar.add(session_dir, arcname=session_id, recursive=True)

    print(f"Bundle created: {tarball_path}")
    print(f"Manifest created: {manifest_path}")
    print(f"Merkle root: {merkle_root}")


if __name__ == "__main__":
    main()
