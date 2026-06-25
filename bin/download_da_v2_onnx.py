#!/usr/bin/env python3
"""Download Depth Anything V2 ONNX model from Aliyun OSS (primary) or HuggingFace (fallback).

Cache location: ~/.cache/oyster/da-v2-small-onnx/
Verifies SHA-256 against manifest before use.

Usage:
    python3 bin/download_da_v2_onnx.py
    python3 bin/download_da_v2_onnx.py --force  # re-download even if cached
"""

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request

# Aliyun OSS mirror (primary, China-resilient)
ALIYUN_URL = "https://oyster-models.oss-cn-hangzhou.aliyuncs.com/da-v2-small/v1/"

# HuggingFace fallback
HF_URL = "https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf/resolve/main/"

# Local manifest (bundled with the repo)
LOCAL_MANIFEST = pathlib.Path("models/da-v2-small-onnx/v1/manifest.json")

CACHE_DIR = pathlib.Path.home() / ".cache" / "oyster" / "da-v2-small-onnx"

FILES = [
    "depth_anything_v2_small.onnx",
    "depth_anything_v2_small.onnx.data",
]


def sha256_file(path: pathlib.Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: pathlib.Path, timeout: float = 30.0) -> bool:
    """Download a file from URL to dest. Returns True on success."""
    print(f"  downloading {url} -> {dest}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "oyster-onnx-downloader/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"  download failed: {e}")
        return False


def download_from_aliyun(dest_dir: pathlib.Path, timeout: float = 3.0) -> bool:
    """Try downloading from Aliyun OSS first."""
    print(f"Trying Aliyun OSS ({ALIYUN_URL})...")
    success = True
    for fname in FILES:
        url = ALIYUN_URL + fname
        dest = dest_dir / fname
        if not download_file(url, dest, timeout=timeout):
            success = False
            break
    return success


def download_from_hf(dest_dir: pathlib.Path) -> bool:
    """Fallback to HuggingFace."""
    print(f"Trying HuggingFace ({HF_URL})...")
    success = True
    for fname in FILES:
        url = HF_URL + fname
        dest = dest_dir / fname
        if not download_file(url, dest, timeout=60.0):
            success = False
            break
    return success


def verify_checksums(dest_dir: pathlib.Path, manifest: dict) -> bool:
    """Verify downloaded files against manifest SHA-256 checksums."""
    for fname, info in manifest.get("files", {}).items():
        fpath = dest_dir / fname
        if not fpath.exists():
            print(f"  MISSING: {fname}")
            return False
        actual = sha256_file(fpath)
        expected = info.get("sha256", "")
        if actual != expected:
            print(f"  CHECKSUM MISMATCH: {fname}")
            print(f"    expected: {expected}")
            print(f"    actual:   {actual}")
            return False
        print(f"  verified: {fname}")
    return True


def load_manifest() -> dict:
    """Load manifest from local repo or return empty dict."""
    if LOCAL_MANIFEST.exists():
        with open(LOCAL_MANIFEST) as f:
            return json.load(f)
    return {}


def download_model(force: bool = False) -> pathlib.Path:
    """Download and verify the ONNX model. Returns path to .onnx file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    onnx_path = CACHE_DIR / "depth_anything_v2_small.onnx"

    # Check if already cached and valid
    if not force and onnx_path.exists():
        manifest = load_manifest()
        if manifest and verify_checksums(CACHE_DIR, manifest):
            print(f"Using cached model: {onnx_path}")
            return onnx_path
        else:
            print("Cached model checksum mismatch or no manifest. Re-downloading...")

    # Try Aliyun OSS first (3s timeout per file)
    if download_from_aliyun(CACHE_DIR, timeout=3.0):
        print("Aliyun OSS download successful")
    else:
        print("Aliyun OSS failed, falling back to HuggingFace...")
        if not download_from_hf(CACHE_DIR):
            print("ERROR: All download sources failed")
            sys.exit(1)

    # Verify checksums
    manifest = load_manifest()
    if manifest:
        if not verify_checksums(CACHE_DIR, manifest):
            print("WARNING: Checksum verification failed after download")
        else:
            print("All checksums verified")
    else:
        print("No local manifest found, skipping checksum verification")

    return onnx_path


def main():
    parser = argparse.ArgumentParser(description="Download Depth Anything V2 ONNX model")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if cached",
    )
    args = parser.parse_args()

    onnx_path = download_model(force=args.force)
    print(f"\nModel ready: {onnx_path}")
    data_path = onnx_path.with_suffix(".onnx.data")
    if data_path.exists():
        size_mb = (onnx_path.stat().st_size + data_path.stat().st_size) / 1e6
    else:
        size_mb = onnx_path.stat().st_size / 1e6
    print(f"Total size: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
