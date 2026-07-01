#!/usr/bin/env python3
"""
fetch_jre.py — Download + verify + extract Eclipse Temurin OpenJDK 21 LTS JRE.

Spec: specs/R05A_bundled_jre_packaging.md

Iron-law constraints:
  * Release URL is PINNED in manifest.json — no "latest" lookups at build time.
  * SHA-256 is PINNED — hard-fails on mismatch (exit 2).
  * stdlib-only — urllib, hashlib, zipfile.
  * Verifies bundle/jre/bin/javaw.exe exists post-extraction.

Usage (CI / local build):
    python bin/build_bundled_installer/fetch_jre.py

Output:
    bundle/jre/                    — extracted portable JRE (top-level dir flattened)
    bundle/jre/bin/javaw.exe       — Windows entry point (verified to exist)
    bundle/jre/manifest.json       — runtime provenance record (pin + actual hashes)

The build-time pin lives in bin/build_bundled_installer/manifest.json. To bump
the JRE: edit that JSON (URL + sha256 + size + version), never edit constants
in this file.

Exit codes:
    0  success
    2  SHA-256 mismatch (corrupt download or upstream tampering)
    3  post-extract verification failed (missing required files, e.g. javaw.exe)
    4  download / network failure after retries
    5  filesystem / extraction error
    6  pin manifest invalid / unreadable
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
PIN_MANIFEST_PATH = SCRIPT_DIR / "manifest.json"

BUNDLE_DIR = REPO_ROOT / "bundle"
JRE_DIR = BUNDLE_DIR / "jre"
CACHE_DIR = REPO_ROOT / "bin" / ".cache" / "jre"
RUNTIME_MANIFEST_PATH = JRE_DIR / "manifest.json"

# Network
DOWNLOAD_TIMEOUT_SEC = 120
DOWNLOAD_RETRIES = 3
DOWNLOAD_CHUNK = 1 << 16  # 64 KiB
USER_AGENT = (
    "oyster-agent-runner-bundled-installer/1.0 (+https://github.com/adoptium)"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(f"[fetch_jre] {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"[fetch_jre] ERROR: {msg}", file=sys.stderr, flush=True)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_pin() -> dict[str, Any]:
    """Read the build-time pin manifest. Hard-fail on missing / malformed."""
    if not PIN_MANIFEST_PATH.is_file():
        _err(f"pin manifest not found: {PIN_MANIFEST_PATH}")
        sys.exit(6)
    try:
        pin = json.loads(PIN_MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _err(f"pin manifest is invalid JSON: {exc}")
        sys.exit(6)

    required_paths = [
        ("source", "url"),
        ("source", "sha256"),
        ("source", "size_bytes"),
        ("source", "filename"),
        ("release_tag",),
        ("post_extract_required_files",),
    ]
    for path in required_paths:
        node: Any = pin
        for key in path:
            if not isinstance(node, dict) or key not in node:
                _err(f"pin manifest missing required field: {'.'.join(path)}")
                sys.exit(6)
            node = node[key]

    if len(pin["source"]["sha256"]) != 64:
        _err("pin manifest: source.sha256 must be a 64-hex-digit SHA-256")
        sys.exit(6)
    return pin


def _download_with_retries(url: str, dest: Path) -> None:
    """Download `url` -> `dest` with bounded retries.

    Streams to a `.part` file then atomically renames to avoid leaving a
    half-written file at `dest` if the process is killed mid-download.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_exc: Exception | None = None

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            _log(f"download attempt {attempt}/{DOWNLOAD_RETRIES}: {url}")
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            # urlopen is fine here: stdlib-only constraint, target URL is pinned.
            with urllib.request.urlopen(  # noqa: S310 (pinned URL, https)
                req, timeout=DOWNLOAD_TIMEOUT_SEC
            ) as resp:
                if tmp.exists():
                    tmp.unlink()
                bytes_read = 0
                with tmp.open("wb") as out:
                    while True:
                        buf = resp.read(DOWNLOAD_CHUNK)
                        if not buf:
                            break
                        out.write(buf)
                        bytes_read += len(buf)
            os.replace(tmp, dest)
            _log(f"downloaded {bytes_read:,} bytes -> {dest}")
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            _err(f"attempt {attempt} failed: {exc}")
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            if attempt < DOWNLOAD_RETRIES:
                backoff = 2 ** (attempt - 1)
                _log(f"retrying in {backoff}s")
                time.sleep(backoff)

    raise RuntimeError(
        f"download failed after {DOWNLOAD_RETRIES} attempts: {last_exc}"
    )


def _verify_sha(path: Path, expected: str) -> None:
    """Hard-fail on SHA-256 mismatch — exit 2."""
    _log(f"verifying SHA-256 of {path.name}")
    got = _sha256_file(path)
    if got.lower() != expected.lower():
        _err(
            "SHA-256 mismatch — refusing to extract. "
            "Possible corrupt download or upstream tampering."
        )
        _err(f"  expected: {expected}")
        _err(f"  got:      {got}")
        try:
            path.unlink()  # don't leave a poisoned cache file behind
        except OSError:
            pass
        sys.exit(2)
    _log(f"SHA-256 OK: {got}")


def _extract_flat(zip_path: Path, dest_dir: Path) -> Path:
    """Extract the JRE zip and flatten the top-level `jdk-21.0.11+10-jre/` dir.

    Temurin zips wrap content in one top-level directory. We extract to a
    staging dir, find that dir, then move it into place at `dest_dir`.

    Returns the resolved JRE root (== dest_dir on success).
    """
    if dest_dir.exists():
        _log(f"removing stale {dest_dir}")
        shutil.rmtree(dest_dir)
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    staging = dest_dir.with_suffix(".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    _log(f"extracting {zip_path.name} -> {staging}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Defense-in-depth: reject any path that escapes staging via .. or
        # absolute paths. Temurin's official zips are clean, but we never
        # trust the bytes blindly.
        for member in zf.namelist():
            normalized = os.path.normpath(member).replace("\\", "/")
            if normalized.startswith(("..", "/")) or ":" in normalized:
                raise RuntimeError(
                    f"refusing to extract suspicious zip entry: {member!r}"
                )
        zf.extractall(staging)

    # Identify the single top-level directory.
    top_entries = [p for p in staging.iterdir() if p.is_dir()]
    if len(top_entries) != 1:
        raise RuntimeError(
            f"expected exactly 1 top-level dir in zip, got {len(top_entries)}: "
            f"{[p.name for p in top_entries]}"
        )
    jre_root = top_entries[0]
    _log(f"flattening {jre_root.name}/ -> {dest_dir.name}/")
    os.replace(jre_root, dest_dir)
    shutil.rmtree(staging, ignore_errors=True)
    return dest_dir


def _verify_post_extract(jre_dir: Path, required_relpaths: list[str]) -> None:
    """Confirm a usable JRE landed on disk.

    Checks every entry in `required_relpaths` (e.g. ``bin/javaw.exe``) exists
    under `jre_dir`. The Windows JRE zip ships ``javaw.exe`` regardless of the
    host OS we extracted on, so this check is meaningful on macOS/Linux CI too.
    """
    bin_dir = jre_dir / "bin"
    if not bin_dir.is_dir():
        contents = (
            sorted(p.name for p in jre_dir.iterdir())
            if jre_dir.is_dir()
            else "<jre_dir missing>"
        )
        _err(f"bundle/jre/bin/ missing after extraction (jre/ contents: {contents})")
        sys.exit(3)

    missing: list[str] = []
    for rel in required_relpaths:
        if not (jre_dir / rel).exists():
            missing.append(rel)

    if missing:
        _err(f"required JRE artifacts missing: {missing}")
        _err(
            "  bin/ contents: "
            f"{sorted(p.name for p in bin_dir.iterdir())[:20]}"
        )
        sys.exit(3)

    _log(f"post-extract verification OK ({len(required_relpaths)} required paths)")


def _dir_size_bytes(root: Path) -> int:
    total = 0
    for dp, _dn, fn in os.walk(root):
        for name in fn:
            try:
                total += (Path(dp) / name).stat().st_size
            except OSError:
                pass
    return total


def _write_runtime_manifest(
    jre_dir: Path, zip_path: Path, pin: dict[str, Any]
) -> None:
    """Emit bundle/jre/manifest.json — runtime provenance record.

    Includes both the build-time pin (so consumers can verify what was
    intended) and the actually-observed hashes/sizes (so we can detect
    bit-rot or tampering after the fact).
    """
    manifest = {
        "schema_version": 1,
        "produced_by": "bin/build_bundled_installer/fetch_jre.py",
        "produced_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pin": {
            "vendor": pin["vendor"],
            "release_tag": pin["release_tag"],
            "version": pin["version"],
            "platform": pin["platform"],
            "source": pin["source"],
        },
        "downloaded": {
            "size_bytes": zip_path.stat().st_size,
            "sha256": _sha256_file(zip_path),
        },
        "extracted": {
            "root": str(jre_dir.relative_to(REPO_ROOT)),
            "size_bytes": _dir_size_bytes(jre_dir),
            "verified_files": pin["post_extract_required_files"],
        },
    }
    RUNTIME_MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    _log(f"wrote {RUNTIME_MANIFEST_PATH.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Download, verify, and extract the pinned JRE bundle.

    Reads the build-time pin manifest to obtain the JRE source URL, expected
    SHA-256 hash, file size, and required post-extraction files. Attempts
    to reuse a cached download if it already passes the size and SHA-256
    verification. Downloads the JRE if needed, verifies the SHA-256 hash
    hard-fail on mismatch, extracts the archive to the bundle directory,
    verifies required files exist (e.g., javaw.exe), and writes a runtime
    provenance manifest.

    Returns:
        Exit code: 0 on success, 2 on SHA mismatch, 3 on post-extract
        verification failure, 4 on download/network failure, 5 on extraction
        failure, 6 on pin manifest invalid/unreadable.
    """
    pin = _load_pin()
    src = pin["source"]
    url: str = src["url"]
    sha: str = src["sha256"]
    size: int = int(src["size_bytes"])
    filename: str = src["filename"]
    required_files: list[str] = list(pin["post_extract_required_files"])

    _log(f"target: {pin['release_tag']} ({pin['version']['full']})")
    _log(f"  url   = {url}")
    _log(f"  sha256= {sha}")
    _log(f"  size  = {size:,} bytes")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached_zip = CACHE_DIR / filename

    # Reuse cached download iff it already passes the SHA pin. Anything else
    # (stale partial, wrong size, hash mismatch) -> re-download.
    needs_download = True
    if cached_zip.is_file() and cached_zip.stat().st_size == size:
        if _sha256_file(cached_zip).lower() == sha.lower():
            _log(f"cache hit: {cached_zip} (SHA-256 OK)")
            needs_download = False
        else:
            _log("cache hash mismatch — re-downloading")
            cached_zip.unlink()

    if needs_download:
        try:
            _download_with_retries(url, cached_zip)
        except Exception as exc:  # noqa: BLE001 (CLI boundary)
            _err(f"download failed: {exc}")
            return 4

    # Hard-fail SHA verification — never extract an unverified blob.
    _verify_sha(cached_zip, sha)

    # Sanity-check size matches what the manifest claims.
    actual_size = cached_zip.stat().st_size
    if actual_size != size:
        _err(f"size mismatch: expected {size}, got {actual_size}")
        return 2

    try:
        jre_root = _extract_flat(cached_zip, JRE_DIR)
    except Exception as exc:  # noqa: BLE001
        _err(f"extraction failed: {exc}")
        return 5

    _verify_post_extract(jre_root, required_files)
    _write_runtime_manifest(jre_root, cached_zip, pin)

    _log(f"done. JRE ready at {jre_root.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
