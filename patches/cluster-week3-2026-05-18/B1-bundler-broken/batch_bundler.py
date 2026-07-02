#!/usr/bin/env python3
"""
Batch Bundler - Bundle multiple finalized session directories into a single deliverable tarball + manifest.

Usage:
    python3 bin/batch_bundler.py <session_dir>... --output-dir <output_dir>
"""

import os
import sys
import json
import hashlib
import tarfile
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple


def sha256_file(filepath: Path) -> str:
    """Calculate SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Calculate SHA256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


def build_merkle_tree(hashes: List[str]) -> str:
    """
    Build a Merkle tree from list of SHA256 hashes (hex strings).
    Returns the Merkle root.
    """
    if not hashes:
        return sha256_bytes(b'')
    
    # Convert hex strings to bytes
    nodes = [bytes.fromhex(h) for h in hashes]
    
    # Pad to power of 2 with zero hashes. Minimum tree size is 2 so a
    # single-leaf root still hashes leaf||zero_hash (matches BIP-style spec).
    n = len(nodes)
    next_power_of_2 = 2  # minimum
    while next_power_of_2 < n:
        next_power_of_2 <<= 1
    
    # Zero hash is sha256 of empty bytes
    zero_hash = sha256_bytes(b'')
    zero_hash_bytes = bytes.fromhex(zero_hash)
    
    # Pad with zero hashes
    nodes.extend([zero_hash_bytes] * (next_power_of_2 - n))
    
    # Build tree bottom-up
    while len(nodes) > 1:
        new_level = []
        for i in range(0, len(nodes), 2):
            left = nodes[i]
            right = nodes[i + 1] if i + 1 < len(nodes) else zero_hash_bytes
            parent = hashlib.sha256(left + right).digest()
            new_level.append(parent)
        nodes = new_level
    
    return nodes[0].hex()


def process_session(session_dir: Path) -> Tuple[str, List[Dict[str, Any]], int, int]:
    """
    Process a session directory.
    Returns: (session_id, file_list, total_files, total_bytes)
    """
    session_id = session_dir.name
    file_list = []
    total_bytes = 0
    
    # Walk through all files in session directory
    for root, dirs, files in os.walk(session_dir):
        for file in files:
            filepath = Path(root) / file
            # Calculate relative path from session directory
            rel_path = filepath.relative_to(session_dir)
            
            # Get file size and hash
            file_size = filepath.stat().st_size
            file_hash = sha256_file(filepath)
            
            file_list.append({
                "path": str(rel_path),
                "sha256": file_hash,
                "bytes": file_size
            })
            
            total_bytes += file_size
    
    # Sort files by path for consistent hashing
    file_list.sort(key=lambda x: x["path"])
    
    # Calculate session SHA256: hash of concatenated file hashes (hex strings)
    concat_hashes = ''.join(f["sha256"] for f in file_list)
    session_sha256 = sha256_bytes(concat_hashes.encode('utf-8'))
    
    return session_id, file_list, len(file_list), total_bytes, session_sha256


def create_tarball(session_dirs: List[Path], output_path: Path) -> str:
    """
    Create tarball containing all session directories.
    Returns SHA256 hash of the tarball.
    """
    with tarfile.open(output_path, 'w:gz') as tar:
        for session_dir in session_dirs:
            session_id = session_dir.name
            for root, dirs, files in os.walk(session_dir):
                for file in files:
                    filepath = Path(root) / file
                    rel_path = filepath.relative_to(session_dir)
                    arcname = f"{session_id}/{rel_path}"
                    tar.add(filepath, arcname=arcname)
    
    # Calculate tarball hash
    return sha256_file(output_path)


def main():
    parser = argparse.ArgumentParser(
        description='Bundle multiple finalized session directories into a single deliverable tarball + manifest.'
    )
    parser.add_argument(
        'session_dirs',
        nargs='+',
        help='Session directories to bundle'
    )
    parser.add_argument(
        '--output-dir',
        required=True,
        help='Output directory for tarball and manifest'
    )
    
    args = parser.parse_args()
    
    # Validate session directories
    session_paths = []
    for session_dir in args.session_dirs:
        path = Path(session_dir)
        if not path.exists():
            print(f"Error: Session directory does not exist: {session_dir}")
            sys.exit(1)
        if not path.is_dir():
            print(f"Error: Not a directory: {session_dir}")
            sys.exit(1)
        session_paths.append(path)
    
    # Create output directory if it doesn't exist
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate batch ID with timestamp
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    batch_id = f"oyster-batch-{timestamp}"
    
    print(f"BATCH BUNDLER — {len(session_paths)} sessions → 1 deliverable")
    
    # Process each session
    all_sessions = []
    all_files = []
    total_files = 0
    total_bytes = 0
    session_hashes = []
    
    for session_dir in session_paths:
        session_id, file_list, file_count, session_bytes, session_sha256 = process_session(session_dir)
        
        all_sessions.append({
            "session_id": session_id,
            "file_count": file_count,
            "bytes": session_bytes,
            "session_sha256": session_sha256,
            "files": file_list
        })
        
        all_files.extend(file_list)
        total_files += file_count
        total_bytes += session_bytes
        session_hashes.append(session_sha256)
        
        print(f"  {session_id}: {file_count} files, {session_bytes / 1024 / 1024:.1f} MB, sha256: {session_sha256[:8]}...")
    
    # Build Merkle tree from all file hashes
    file_hashes = [f["sha256"] for f in all_files]
    merkle_root = build_merkle_tree(file_hashes)
    
    print(f"\n  Total: {total_files} files, {total_bytes / 1024 / 1024:.1f} MB")
    print(f"  Merkle root: {merkle_root[:8]}...")
    
    # Create tarball
    tarball_filename = f"{batch_id}.tar.gz"
    tarball_path = output_dir / tarball_filename
    tarball_sha256 = create_tarball(session_paths, tarball_path)
    
    print(f"  Tarball: {tarball_path}")
    
    # Create manifest
    manifest = {
        "batch_id": batch_id,
        "created_at_utc": datetime.utcnow().isoformat() + "Z",
        "session_count": len(session_paths),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "merkle_root": merkle_root,
        "sessions": all_sessions,
        "tarball_filename": tarball_filename,
        "tarball_sha256": tarball_sha256
    }
    
    manifest_filename = f"{batch_id}.manifest.json"
    manifest_path = output_dir / manifest_filename
    
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    print(f"  Manifest: {manifest_path}")
    
    # Print summary
    print("\n✓ Batch created successfully!")
    print(f"  Batch ID: {batch_id}")
    print(f"  Tarball size: {tarball_path.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"  Tarball SHA256: {tarball_sha256[:8]}...")


if __name__ == "__main__":
    main()
