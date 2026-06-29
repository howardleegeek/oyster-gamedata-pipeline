#!/usr/bin/env python3
"""
fetch_minecraft.py — Download + verify vanilla Minecraft 1.21.4 client + libs.

Spec: specs/R05B_bundled_minecraft_packaging.md

Iron-law constraints:
  * Version ID is PINNED in manifest.json (mc_pin.version_id == "1.21.4").
  * SHA-1 for every artifact comes from Mojang's per-version manifest and
    is RE-VERIFIED after download — hard-fail on mismatch (exit 2).
  * stdlib-only — urllib, hashlib, json, pathlib.
  * Library rules engine matches the official Minecraft launcher's logic
    so we ship the right natives for the target platform (Windows x64).
  * Asset objects (~4035 files / ~390 MB) ARE downloaded in this build
    (rc7 fix). Earlier rc6 shipped only the asset INDEX json; without the
    actual asset OBJECTS (textures / models / sounds / langs) MC starts
    but cannot render — emits "Missing model for variant: ..." spam and
    javaw crashes before the main menu paints. The objects live at
    https://resources.download.minecraft.net/<first-2-of-hash>/<full-hash>
    and are stored on disk under the same hash-prefix layout the vanilla
    launcher uses, so MC's AssetIndex loader picks them up unchanged.
    A bounded thread pool (8 workers) takes the wall time from ~10 min
    serial to ~3-5 min. Resume support: file present + correct size +
    correct SHA-1 == cache hit, no network.
  * Output layout mirrors what bin/oyster_launch_mc.py expects:
        bundle/mc-instance/
          versions/1.21.4/
            1.21.4.jar               (vanilla client)
            1.21.4.json              (Mojang's per-version manifest, verbatim)
          libraries/<group>/<artifact>/<version>/<artifact>-<version>.jar
          assets/indexes/19.json     (asset index — ~370KB)
          assets/objects/<2-char>/<sha1>          (~4035 files, ~390 MB)

Usage (CI / local build):
    python bin/build_bundled_installer/fetch_minecraft.py

Exit codes:
    0  success
    2  SHA-1 / SHA-256 mismatch (corrupt download or upstream tampering)
    3  post-fetch verification failed (required files missing)
    4  download / network failure after retries
    5  filesystem / IO error
    6  pin manifest invalid / unreadable
    7  Mojang version manifest invalid (1.21.4 not found, or schema drift)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Asset-object fetch tunables
# ---------------------------------------------------------------------------
# Mojang's CDN for asset objects (textures, sounds, lang files, models, ...).
# Layout: <base>/<first-2-chars-of-hash>/<full-hash>  — no extension, the
# file is content-addressed by SHA-1 and the launcher's AssetIndex loader
# resolves the original path via the index JSON.
ASSET_OBJECT_BASE_URL = "https://resources.download.minecraft.net"
# 8 workers is the sweet spot on Mojang's CDN (Cloudfront): more risks
# 503s during sustained bursts; fewer leaves bandwidth on the table.
ASSET_FETCH_WORKERS = 8
# Print progress every N objects so a 4035-object run isn't silent.
ASSET_PROGRESS_EVERY = 250

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
PIN_MANIFEST_PATH = SCRIPT_DIR / "manifest.json"

BUNDLE_DIR = REPO_ROOT / "bundle"
MC_INSTANCE_DIR = BUNDLE_DIR / "mc-instance"
CACHE_DIR = REPO_ROOT / "bin" / ".cache" / "mc"
RUNTIME_MANIFEST_PATH = MC_INSTANCE_DIR / "manifest-mc.json"

# Network
DOWNLOAD_TIMEOUT_SEC = 120
DOWNLOAD_RETRIES = 3
DOWNLOAD_CHUNK = 1 << 16  # 64 KiB
USER_AGENT = "oyster-agent-runner-bundled-installer/1.0 (+https://piston-meta.mojang.com)"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(f"[fetch_mc] {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"[fetch_mc] ERROR: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()  # noqa: S324 — Mojang's manifest pins SHA-1, not us.
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Pin manifest
# ---------------------------------------------------------------------------


def _load_pin() -> dict[str, Any]:
    """Read the build-time pin manifest. Hard-fail on missing / malformed."""
    if not PIN_MANIFEST_PATH.is_file():
        _err(f"pin manifest not found: {PIN_MANIFEST_PATH}")
        sys.exit(6)
    try:
        full_pin = json.loads(PIN_MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _err(f"pin manifest is invalid JSON: {exc}")
        sys.exit(6)

    if "mc_pin" not in full_pin or not isinstance(full_pin["mc_pin"], dict):
        _err("pin manifest missing required field: mc_pin")
        sys.exit(6)

    pin = full_pin["mc_pin"]
    required = [
        "version_id",
        "asset_index_id",
        "version_manifest_url",
        "platform",
        "post_fetch_required_files",
    ]
    for key in required:
        if key not in pin:
            _err(f"pin manifest missing required field: mc_pin.{key}")
            sys.exit(6)
    if "os" not in pin["platform"] or "arch" not in pin["platform"]:
        _err("pin manifest missing mc_pin.platform.os or .arch")
        sys.exit(6)
    return pin


# ---------------------------------------------------------------------------
# Download primitives
# ---------------------------------------------------------------------------


def _download_with_retries(url: str, dest: Path) -> int:
    """Download `url` -> `dest` with bounded retries. Returns bytes written.

    Streams to a `.part` file then atomically renames so we never leave a
    half-written file at `dest` if the process is killed mid-download.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_exc: Exception | None = None

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(  # noqa: S310 — pinned https URL
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
            return bytes_read
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if tmp.exists():
                with suppress(OSError):
                    tmp.unlink()
            if attempt < DOWNLOAD_RETRIES:
                backoff = 2 ** (attempt - 1)
                _log(
                    f"  attempt {attempt}/{DOWNLOAD_RETRIES} failed: {exc}; retrying in {backoff}s"
                )
                time.sleep(backoff)
            else:
                _err(f"  attempt {attempt}/{DOWNLOAD_RETRIES} failed: {exc}; giving up")

    raise RuntimeError(f"download failed after {DOWNLOAD_RETRIES} attempts: {last_exc}")


def _download_json(url: str) -> dict[str, Any]:
    """Fetch a JSON document with retries. Used for Mojang manifests."""
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(  # noqa: S310 — pinned https URL
                req, timeout=DOWNLOAD_TIMEOUT_SEC
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_exc = exc
            if attempt < DOWNLOAD_RETRIES:
                backoff = 2 ** (attempt - 1)
                _log(
                    f"  json fetch attempt {attempt}/{DOWNLOAD_RETRIES} failed: "
                    f"{exc}; retrying in {backoff}s"
                )
                time.sleep(backoff)
    raise RuntimeError(f"JSON fetch failed after {DOWNLOAD_RETRIES} attempts: {last_exc}")


def _fetch_with_sha1_pin(
    url: str,
    dest: Path,
    expected_sha1: str,
    expected_size: int | None,
    label: str,
) -> int:
    """Download `url` to `dest` and hard-fail on SHA-1 mismatch.

    Reuses cached file iff it already passes the SHA-1 pin and size match.
    Returns bytes downloaded (0 if cache hit).
    """
    if dest.is_file() and (expected_size is None or dest.stat().st_size == expected_size):
        if _sha1_file(dest).lower() == expected_sha1.lower():
            return 0  # cache hit
        # Stale / corrupt cache — re-download.
        with suppress(OSError):
            dest.unlink()

    try:
        bytes_dl = _download_with_retries(url, dest)
    except Exception as exc:  # noqa: BLE001 (CLI boundary)
        _err(f"{label}: download failed: {exc}")
        sys.exit(4)

    got = _sha1_file(dest)
    if got.lower() != expected_sha1.lower():
        _err(f"{label}: SHA-1 mismatch — refusing to keep file. expected={expected_sha1} got={got}")
        with suppress(OSError):
            dest.unlink()
        sys.exit(2)

    if expected_size is not None and dest.stat().st_size != expected_size:
        _err(f"{label}: size mismatch — expected {expected_size}, got {dest.stat().st_size}")
        sys.exit(2)

    return bytes_dl


# ---------------------------------------------------------------------------
# Mojang library rules engine
# ---------------------------------------------------------------------------


def _evaluate_rules(rules: list[dict[str, Any]] | None, target_os: str) -> bool:
    """Evaluate a Mojang library `rules` block for the target OS.

    Default action when no rules are present is "allow".

    Each rule has an `action` (allow|disallow) and an optional `os` clause
    naming `windows`, `linux`, or `osx`. Later rules override earlier ones.
    See: https://minecraft.wiki/w/Version_manifest.json#Rules
    """
    if not rules:
        return True

    # Map our pin's `os` value to Mojang's naming.
    os_alias = {"windows": "windows", "linux": "linux", "osx": "osx", "macos": "osx"}
    target = os_alias.get(target_os, target_os)

    allowed = False  # without rules + matching default-allow, walk decides
    for rule in rules:
        action = rule.get("action", "allow")
        os_clause = rule.get("os")
        applies = True
        if os_clause is not None:
            os_name = os_clause.get("name")
            if os_name is not None and os_name != target:
                applies = False
        if applies:
            allowed = action == "allow"
    return allowed


def _maven_path(name: str) -> Path:
    """Convert Mojang/Maven coord 'group:artifact:version[:classifier]' to relpath.

    Examples:
        org.lwjgl:lwjgl:3.3.3
            -> org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3.jar
        org.lwjgl:lwjgl:3.3.3:natives-windows
            -> org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-windows.jar
    """
    parts = name.split(":")
    if len(parts) < 3:
        raise ValueError(f"invalid maven coord: {name!r}")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) >= 4 else None

    group_path = group.replace(".", "/")
    if classifier:
        filename = f"{artifact}-{version}-{classifier}.jar"
    else:
        filename = f"{artifact}-{version}.jar"
    return Path(group_path) / artifact / version / filename


# ---------------------------------------------------------------------------
# Per-version manifest loader
# ---------------------------------------------------------------------------


def _resolve_version_manifest_url(version_manifest_url: str, version_id: str) -> tuple[str, str]:
    """Find the per-version manifest URL + sha1 for `version_id` in the index.

    Returns (manifest_url, manifest_sha1). Hard-fails if the version isn't
    listed (Mojang occasionally retires very old versions; 1.21.4 must
    remain forever per Mojang policy).
    """
    _log(f"fetching version index: {version_manifest_url}")
    index = _download_json(version_manifest_url)
    for v in index.get("versions", []):
        if v.get("id") == version_id:
            url = v.get("url")
            sha1 = v.get("sha1")
            if not url or not sha1:
                _err(f"version_manifest_v2.json entry for {version_id} is malformed")
                sys.exit(7)
            return url, sha1
    _err(
        f"version {version_id!r} not found in Mojang version_manifest_v2.json — "
        f"this should never happen for 1.21.4. Check the URL pin in manifest.json."
    )
    sys.exit(7)


# ---------------------------------------------------------------------------
# Stage 1: client.jar + per-version manifest JSON
# ---------------------------------------------------------------------------


def _fetch_per_version_manifest(
    manifest_url: str,
    expected_sha1: str,
    version_id: str,
) -> dict[str, Any]:
    """Download + verify the per-version manifest. Saves verbatim to disk
    and returns the parsed dict."""
    versions_dir = MC_INSTANCE_DIR / "versions" / version_id
    versions_dir.mkdir(parents=True, exist_ok=True)
    json_path = versions_dir / f"{version_id}.json"

    cache_path = CACHE_DIR / f"{version_id}.json"
    _fetch_with_sha1_pin(
        url=manifest_url,
        dest=cache_path,
        expected_sha1=expected_sha1,
        expected_size=None,  # Mojang doesn't pin size for manifest JSON
        label=f"per-version manifest {version_id}",
    )
    # Copy into final location verbatim (oyster_launch_mc.py reads it from there).
    json_path.write_bytes(cache_path.read_bytes())

    return json.loads(cache_path.read_text(encoding="utf-8"))


def _fetch_client_jar(per_version: dict[str, Any], version_id: str) -> int:
    """Download + verify client.jar. Returns bytes downloaded."""
    downloads = per_version.get("downloads", {})
    client = downloads.get("client")
    if not client or "url" not in client or "sha1" not in client or "size" not in client:
        _err("per-version manifest missing downloads.client.{url,sha1,size}")
        sys.exit(7)

    versions_dir = MC_INSTANCE_DIR / "versions" / version_id
    jar_path = versions_dir / f"{version_id}.jar"
    cache_path = CACHE_DIR / f"{version_id}.jar"

    bytes_dl = _fetch_with_sha1_pin(
        url=client["url"],
        dest=cache_path,
        expected_sha1=client["sha1"],
        expected_size=int(client["size"]),
        label=f"client.jar ({version_id})",
    )
    if not jar_path.is_file() or jar_path.stat().st_size != int(client["size"]):
        jar_path.write_bytes(cache_path.read_bytes())
    return bytes_dl


# ---------------------------------------------------------------------------
# Stage 2: libraries
# ---------------------------------------------------------------------------


def _fetch_libraries(per_version: dict[str, Any], target_os: str) -> tuple[int, int, int]:
    """Download every library (matching the rules) into bundle/mc-instance/libraries.

    Returns (bytes_downloaded, libraries_kept, libraries_skipped).

    Notes on Mojang's schema as of 1.21.4:
      * Each library may have `downloads.artifact` (the main jar, optional).
      * Each library may have `downloads.classifiers` for natives, but
        *modern* MC versions (1.19+) actually inline natives as separate
        library entries with their own `rules` rather than using the
        legacy `natives.<os>` indirection. We support both shapes.
      * `rules` filter the library by OS — must be evaluated.
    """
    libs_root = MC_INSTANCE_DIR / "libraries"
    libs_root.mkdir(parents=True, exist_ok=True)

    libraries = per_version.get("libraries", [])
    if not isinstance(libraries, list) or not libraries:
        _err("per-version manifest has no libraries[] block — corrupt?")
        sys.exit(7)

    bytes_dl = 0
    kept = 0
    skipped = 0

    for lib in libraries:
        if not _evaluate_rules(lib.get("rules"), target_os):
            skipped += 1
            continue

        downloads = lib.get("downloads", {})
        # Main artifact (most libs).
        artifact = downloads.get("artifact")
        if artifact and "url" in artifact and "sha1" in artifact:
            url = artifact["url"]
            sha1 = artifact["sha1"]
            size = int(artifact.get("size", 0)) or None
            rel = artifact.get("path")
            if not rel:
                # Fall back to maven-coord derivation.
                rel = str(_maven_path(lib["name"]))
            dest = libs_root / rel
            cache = CACHE_DIR / "libs" / rel

            bytes_dl += _fetch_with_sha1_pin(
                url=url,
                dest=cache,
                expected_sha1=sha1,
                expected_size=size,
                label=f"library {lib.get('name', rel)}",
            )
            if not dest.is_file() or (size is not None and dest.stat().st_size != size):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(cache.read_bytes())
            kept += 1

        # Legacy `classifiers` block (pre-1.19) — handle defensively even
        # though 1.21.4 doesn't use it. natives clauses tell us which
        # classifier maps to the current OS.
        natives = lib.get("natives") or {}
        os_alias = {"windows": "windows", "linux": "linux", "osx": "osx", "macos": "osx"}
        native_classifier = natives.get(os_alias.get(target_os, target_os))
        if native_classifier and downloads.get("classifiers"):
            cls_block = downloads["classifiers"].get(native_classifier)
            if cls_block and "url" in cls_block and "sha1" in cls_block:
                url = cls_block["url"]
                sha1 = cls_block["sha1"]
                size = int(cls_block.get("size", 0)) or None
                rel = cls_block.get("path")
                if not rel:
                    rel = str(_maven_path(f"{lib['name']}:{native_classifier}"))
                dest = libs_root / rel
                cache = CACHE_DIR / "libs" / rel

                bytes_dl += _fetch_with_sha1_pin(
                    url=url,
                    dest=cache,
                    expected_sha1=sha1,
                    expected_size=size,
                    label=f"native {lib.get('name', rel)}:{native_classifier}",
                )
                if not dest.is_file() or (size is not None and dest.stat().st_size != size):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(cache.read_bytes())
                kept += 1

    return bytes_dl, kept, skipped


# ---------------------------------------------------------------------------
# Stage 2b: Extract LWJGL / native .dll files into versions/<id>/natives/
# ---------------------------------------------------------------------------


def _is_target_native_classifier(name: str, target_arch: str) -> bool:
    """Return True if this maven coord is the natives jar for our target arch.

    Modern (1.19+) Mojang manifests inline natives as separate library
    entries with maven coords like:

        org.lwjgl:lwjgl:3.3.3:natives-windows         (x64)
        org.lwjgl:lwjgl:3.3.3:natives-windows-arm64
        org.lwjgl:lwjgl:3.3.3:natives-windows-x86

    The OS rule on each entry is identical ("windows"), so the rules
    engine alone cannot discriminate. We pick the right jar by classifier
    suffix: bare ``natives-windows`` is x64; explicit ``-arm64`` / ``-x86``
    suffixes belong to the other archs and we drop them.
    """
    parts = name.split(":")
    if len(parts) < 4:
        return False  # no classifier
    classifier = parts[3]

    # Bug 1 fix: Windows x64 only — the bare ``natives-windows`` classifier.
    # Skip ``-arm64`` and ``-x86`` even though their library rules also
    # ``allow`` os.name=windows (Mojang doesn't put arch in the rule).
    if target_arch == "x64":
        return classifier == "natives-windows"
    # We don't ship arm64 / x86 builds; if anyone ever does, just match
    # the obvious classifier name.
    return classifier == f"natives-windows-{target_arch}"


def _extract_dlls_from_jar(jar_path: Path, natives_dir: Path) -> int:
    """Extract every ``*.dll`` entry from a native classifier jar (flat).

    The DLL inside an LWJGL natives jar is at a deep path like
    ``windows/x64/org/lwjgl/lwjgl.dll`` (or in jtracy's case, just
    ``jtracy-jni-windows.dll`` at the root). Java's
    ``-Djava.library.path`` is a flat search path, so we drop every
    extracted DLL into ``natives_dir`` keyed only by its basename.

    Returns the number of DLLs extracted.
    """
    natives_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            for member in zf.namelist():
                if not member.lower().endswith(".dll"):
                    continue
                # Defence: skip anything that escapes the jar via "..".
                if ".." in Path(member).parts:
                    _err(f"refusing dll path with .. in jar {jar_path.name}: {member}")
                    continue
                basename = Path(member).name
                target = natives_dir / basename
                with zf.open(member) as src, target.open("wb") as dst:
                    while True:
                        buf = src.read(DOWNLOAD_CHUNK)
                        if not buf:
                            break
                        dst.write(buf)
                extracted += 1
    except (zipfile.BadZipFile, OSError) as exc:
        _err(f"could not extract dlls from {jar_path}: {exc}")
        sys.exit(5)
    return extracted


def _extract_natives(
    per_version: dict[str, Any],
    target_os: str,
    target_arch: str,
    version_id: str,
) -> tuple[int, int]:
    """Extract Windows-x64 native ``.dll``s from already-cached classifier jars.

    Bug 1 fix (R05B): the existing library walk downloads the
    ``*:natives-windows`` jars but never extracts the DLLs out of them,
    so MC crashes at startup with::

        UnsatisfiedLinkError: Failed to locate library: lwjgl.dll

    This second pass:
      1. Iterates ``libraries[]`` again.
      2. Honors each entry's ``rules`` block (so a Linux-only entry is
         skipped even if it carries a Windows-looking classifier).
      3. Selects entries whose maven coord ends in ``:natives-windows``
         (Windows x64 — explicitly skipping ``-arm64`` / ``-x86``).
      4. Extracts every ``.dll`` from the cached jar into
         ``bundle/mc-instance/versions/<version_id>/natives/``.

    Returns (jars_processed, dlls_extracted).
    """
    natives_dir = MC_INSTANCE_DIR / "versions" / version_id / "natives"
    libraries = per_version.get("libraries", [])
    jars_processed = 0
    dlls_extracted = 0

    for lib in libraries:
        name = lib.get("name")
        if not isinstance(name, str):
            continue
        if not _is_target_native_classifier(name, target_arch):
            continue
        if not _evaluate_rules(lib.get("rules"), target_os):
            continue

        artifact = lib.get("downloads", {}).get("artifact") or {}
        rel = artifact.get("path")
        if not rel:
            try:
                rel = str(_maven_path(name))
            except ValueError:
                continue
        cache_jar = CACHE_DIR / "libs" / rel
        if not cache_jar.is_file():
            # _fetch_libraries should have produced this; log + skip if not.
            _err(f"natives jar missing in cache (expected for {name}): {cache_jar}")
            continue

        n = _extract_dlls_from_jar(cache_jar, natives_dir)
        _log(f"  extracted {n} dll(s) from {name}")
        jars_processed += 1
        dlls_extracted += n

    return jars_processed, dlls_extracted


# ---------------------------------------------------------------------------
# Stage 3: asset index JSON (no objects!)
# ---------------------------------------------------------------------------


def _fetch_asset_index(per_version: dict[str, Any], expected_id: str) -> int:
    """Download + verify the asset index JSON. Returns bytes downloaded.

    The actual asset OBJECTS (~4035 files / ~390 MB referenced by this
    index) are fetched separately by ``_fetch_asset_objects`` — kept as
    two passes so a crash mid-objects doesn't have to re-verify the
    SHA-1-pinned index json.
    """
    asset_index = per_version.get("assetIndex")
    if not asset_index or "url" not in asset_index or "sha1" not in asset_index:
        _err("per-version manifest missing assetIndex.{url,sha1}")
        sys.exit(7)

    actual_id = asset_index.get("id")
    if actual_id != expected_id:
        _log(
            f"  warning: assetIndex.id = {actual_id!r}, "
            f"manifest pin = {expected_id!r} — using upstream ({actual_id!r})"
        )
        expected_id = actual_id  # Trust upstream over our pin.

    indexes_dir = MC_INSTANCE_DIR / "assets" / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    final_path = indexes_dir / f"{expected_id}.json"
    cache_path = CACHE_DIR / "assets" / f"{expected_id}.json"

    bytes_dl = _fetch_with_sha1_pin(
        url=asset_index["url"],
        dest=cache_path,
        expected_sha1=asset_index["sha1"],
        expected_size=int(asset_index.get("size", 0)) or None,
        label=f"asset index {expected_id}",
    )
    if not final_path.is_file():
        final_path.write_bytes(cache_path.read_bytes())
    return bytes_dl


# ---------------------------------------------------------------------------
# Stage 3b: asset OBJECTS (textures / sounds / models — ~390 MB, ~4035 files)
# ---------------------------------------------------------------------------


def _asset_object_relpath(sha1: str) -> Path:
    """Return ``<2-char-prefix>/<full-sha1>`` — Mojang's content-addressed layout.

    Files are stored by hash, no extension. MC's AssetIndex loader walks
    objects/<prefix>/<hash> when resolving a logical asset name, so this
    layout is what the launcher already expects.
    """
    if len(sha1) < 2:
        raise ValueError(f"asset entry sha1 too short: {sha1!r}")
    return Path(sha1[:2]) / sha1


def _fetch_one_asset_object(
    logical_name: str,
    sha1: str,
    size: int,
    objects_root: Path,
) -> tuple[str, str, int, bool]:
    """Worker: fetch one asset object (or hit cache) + SHA-1 verify.

    Returns (logical_name, sha1, bytes_downloaded, was_cache_hit).

    Raises on ANY anomaly — caller (the executor) propagates the exception
    back up so the run aborts with a clean stack trace. Hard-fail policy
    matches every other stage: the only acceptable exit for a SHA-1
    mismatch is non-zero.
    """
    rel = _asset_object_relpath(sha1)
    dest = objects_root / rel

    # Resume support: present + correct size + correct SHA-1 == skip network.
    if dest.is_file() and dest.stat().st_size == size:
        if _sha1_file(dest).lower() == sha1.lower():
            return logical_name, sha1, 0, True
        # Stale / corrupt — re-download.
        with suppress(OSError):
            dest.unlink()

    url = f"{ASSET_OBJECT_BASE_URL}/{sha1[:2]}/{sha1}"
    try:
        bytes_dl = _download_with_retries(url, dest)
    except Exception as exc:  # noqa: BLE001 — re-raise as RuntimeError below
        raise RuntimeError(f"asset object {logical_name!r} ({sha1}): {exc}") from exc

    got = _sha1_file(dest)
    if got.lower() != sha1.lower():
        with suppress(OSError):
            dest.unlink()
        raise RuntimeError(
            f"asset object {logical_name!r}: SHA-1 mismatch — expected={sha1} got={got}"
        )

    actual = dest.stat().st_size
    if actual != size:
        with suppress(OSError):
            dest.unlink()
        raise RuntimeError(
            f"asset object {logical_name!r}: size mismatch — expected={size} got={actual}"
        )

    return logical_name, sha1, bytes_dl, False


def _fetch_asset_objects(asset_index_id: str) -> tuple[int, int, int, int]:
    """Fetch every asset object referenced by ``assets/indexes/<id>.json``.

    Mojang's index file is a JSON ``{ "objects": { "<logical_name>": {
    "hash": "<sha1>", "size": <int> }, ... } }``. We walk that map, fan
    out across ``ASSET_FETCH_WORKERS`` threads, and SHA-1-verify every
    payload against the declared hash. Already-correct files on disk are
    skipped (resume / re-run safety).

    Returns (objects_total, cache_hits, downloaded, bytes_downloaded).
    """
    index_path = MC_INSTANCE_DIR / "assets" / "indexes" / f"{asset_index_id}.json"
    if not index_path.is_file():
        _err(f"asset index missing — cannot fetch objects: {index_path}")
        sys.exit(3)

    try:
        index_doc = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _err(f"asset index JSON unreadable: {exc}")
        sys.exit(7)

    objects = index_doc.get("objects")
    if not isinstance(objects, dict) or not objects:
        _err("asset index has no objects[] map — corrupt?")
        sys.exit(7)

    # De-dup by hash before scheduling work. Many logical names share the same
    # hash; if we submit duplicate hashes concurrently, two workers can write
    # the same ``<hash>.part`` path and race each other. That showed up in CI
    # as all 4038 futures succeeding while a required sampled object vanished
    # by post-fetch verification.
    objects_root = MC_INSTANCE_DIR / "assets" / "objects"
    objects_root.mkdir(parents=True, exist_ok=True)

    total = len(objects)
    unique_objects: dict[str, tuple[str, int]] = {}
    for logical_name, entry in objects.items():
        sha1 = entry.get("hash")
        size = entry.get("size")
        if not isinstance(sha1, str) or not isinstance(size, int):
            _err(f"asset index entry malformed for {logical_name!r}: hash={sha1!r} size={size!r}")
            sys.exit(7)
        unique_objects.setdefault(sha1, (logical_name, size))

    _log(
        f"  asset objects: {total} entries, {len(unique_objects)} unique "
        f"hashes to verify (parallel workers={ASSET_FETCH_WORKERS})"
    )

    bytes_dl_total = 0
    cache_hits = 0
    downloaded = 0
    completed = 0

    # ThreadPoolExecutor uses daemon threads + a small queue, so a hard
    # SIGINT propagates cleanly. Iterating ``as_completed`` lets us stream
    # progress + abort fast on the first hard-fail.
    with ThreadPoolExecutor(max_workers=ASSET_FETCH_WORKERS) as pool:
        futures = []
        for sha1, (logical_name, size) in unique_objects.items():
            futures.append(
                pool.submit(
                    _fetch_one_asset_object,
                    logical_name,
                    sha1,
                    size,
                    objects_root,
                )
            )

        for fut in as_completed(futures):
            try:
                _name, _sha, bytes_dl, was_cached = fut.result()
            except Exception as exc:  # noqa: BLE001 — pool boundary
                # Cancel everything still pending and bail. SHA-1 mismatch
                # is exit 2 (matches other stages); anything else is 4.
                for f in futures:
                    f.cancel()
                msg = str(exc)
                _err(msg)
                if "SHA-1 mismatch" in msg or "size mismatch" in msg:
                    sys.exit(2)
                sys.exit(4)

            bytes_dl_total += bytes_dl
            if was_cached:
                cache_hits += 1
            else:
                downloaded += 1
            completed += 1
            if completed % ASSET_PROGRESS_EVERY == 0 or completed == total:
                _log(
                    f"    progress: {completed}/{total} "
                    f"(downloaded={downloaded}, cached={cache_hits}, "
                    f"bytes_dl={bytes_dl_total:,})"
                )

    return total, cache_hits, downloaded, bytes_dl_total


# ---------------------------------------------------------------------------
# Post-fetch verification + runtime manifest
# ---------------------------------------------------------------------------


def _verify_post_fetch(required_relpaths: list[str]) -> None:
    missing: list[str] = []
    for rel in required_relpaths:
        if not (MC_INSTANCE_DIR / rel).is_file():
            missing.append(rel)
    if missing:
        _err(f"required MC artifacts missing: {missing}")
        sys.exit(3)
    _log(f"post-fetch verification OK ({len(required_relpaths)} required files)")


def _dir_size_bytes(root: Path) -> int:
    total = 0
    for dp, _dn, fn in os.walk(root):
        for name in fn:
            with suppress(OSError):
                total += (Path(dp) / name).stat().st_size
    return total


def _write_runtime_manifest(
    pin: dict[str, Any],
    per_version: dict[str, Any],
    bytes_downloaded: int,
    libraries_kept: int,
    libraries_skipped: int,
    natives_jars: int = 0,
    natives_dlls: int = 0,
    asset_objects_total: int = 0,
    asset_objects_downloaded: int = 0,
    asset_objects_cached: int = 0,
) -> None:
    """Emit bundle/mc-instance/manifest-mc.json — runtime provenance record."""
    client_dl = per_version.get("downloads", {}).get("client", {})
    asset_index = per_version.get("assetIndex", {})

    manifest = {
        "schema_version": 1,
        "produced_by": "bin/build_bundled_installer/fetch_minecraft.py",
        "produced_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pin": {
            "vendor": pin.get("vendor", "mojang"),
            "version_id": pin["version_id"],
            "asset_index_id": pin["asset_index_id"],
            "platform": pin["platform"],
        },
        "upstream": {
            "version_manifest_url": pin["version_manifest_url"],
            "client_url": client_dl.get("url"),
            "client_sha1": client_dl.get("sha1"),
            "client_size_bytes": client_dl.get("size"),
            "asset_index_url": asset_index.get("url"),
            "asset_index_sha1": asset_index.get("sha1"),
            "asset_index_id_resolved": asset_index.get("id"),
        },
        "result": {
            "libraries_kept": libraries_kept,
            "libraries_skipped_for_os": libraries_skipped,
            "natives_jars_processed": natives_jars,
            "natives_dlls_extracted": natives_dlls,
            "asset_objects_total": asset_objects_total,
            "asset_objects_downloaded_this_run": asset_objects_downloaded,
            "asset_objects_cache_hits": asset_objects_cached,
            "bytes_downloaded_this_run": bytes_downloaded,
            "instance_size_bytes_total": _dir_size_bytes(MC_INSTANCE_DIR),
        },
    }
    RUNTIME_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _log(f"wrote {RUNTIME_MANIFEST_PATH.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    pin = _load_pin()
    version_id: str = pin["version_id"]
    asset_index_id: str = pin["asset_index_id"]
    target_os: str = pin["platform"]["os"]
    version_manifest_url: str = pin["version_manifest_url"]
    required_files: list[str] = list(pin["post_fetch_required_files"])

    _log(f"target: Minecraft {version_id} ({target_os}-{pin['platform']['arch']})")
    _log(f"  asset_index_id = {asset_index_id}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MC_INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

    # Stage 0: discover the per-version manifest URL.
    pv_url, pv_sha1 = _resolve_version_manifest_url(version_manifest_url, version_id)
    _log(f"  per-version manifest: {pv_url}")
    _log(f"  per-version sha1    : {pv_sha1}")

    bytes_dl_total = 0

    # Stage 1: per-version manifest + client.jar
    try:
        per_version = _fetch_per_version_manifest(pv_url, pv_sha1, version_id)
    except Exception as exc:  # noqa: BLE001
        _err(f"failed to fetch per-version manifest: {exc}")
        return 4

    _log(f"fetching client.jar ({version_id})")
    bytes_dl_total += _fetch_client_jar(per_version, version_id)

    # Stage 2: libraries
    _log(f"fetching libraries (target OS: {target_os})")
    libs_bytes, libs_kept, libs_skipped = _fetch_libraries(per_version, target_os)
    bytes_dl_total += libs_bytes
    _log(f"  libraries: {libs_kept} kept, {libs_skipped} skipped (rules)")

    # Stage 2b: extract native .dlls from windows-x64 classifier jars
    target_arch: str = pin["platform"]["arch"]
    _log(f"extracting native dlls (target arch: {target_arch})")
    natives_jars, natives_dlls = _extract_natives(per_version, target_os, target_arch, version_id)
    _log(f"  natives: {natives_dlls} dll(s) extracted from {natives_jars} classifier jar(s)")

    # Stage 3: asset index JSON
    _log(f"fetching asset index {asset_index_id}")
    bytes_dl_total += _fetch_asset_index(per_version, asset_index_id)

    # Resolve the *actual* upstream asset index id. Mojang's per-version
    # manifest is the source of truth — our pin is just a sanity guard,
    # and ``_fetch_asset_index`` already silently prefers upstream when
    # they disagree. Re-read it here so ``_fetch_asset_objects`` opens
    # the file we actually wrote (e.g. ``assets/indexes/19.json``).
    resolved_asset_id: str = per_version.get("assetIndex", {}).get("id") or asset_index_id

    # Stage 3b: asset OBJECTS — ~4035 files / ~390 MB, parallelised.
    _log(f"fetching asset objects for index {resolved_asset_id!r} (parallel pool, SHA-1 verified)")
    objs_total, objs_cached, objs_dl, objs_bytes = _fetch_asset_objects(resolved_asset_id)
    bytes_dl_total += objs_bytes
    _log(
        f"  asset objects: {objs_total} total, {objs_dl} downloaded, "
        f"{objs_cached} cache-hit, {objs_bytes:,} bytes downloaded"
    )

    # Verify + write runtime manifest.
    _verify_post_fetch(required_files)
    _write_runtime_manifest(
        pin,
        per_version,
        bytes_dl_total,
        libs_kept,
        libs_skipped,
        natives_jars=natives_jars,
        natives_dlls=natives_dlls,
        asset_objects_total=objs_total,
        asset_objects_downloaded=objs_dl,
        asset_objects_cached=objs_cached,
    )

    instance_size = _dir_size_bytes(MC_INSTANCE_DIR)
    _log(
        f"done. bundle/mc-instance/ = {instance_size:,} bytes ({instance_size / (1 << 20):.1f} MiB)"
    )
    _log(f"  (downloaded this run: {bytes_dl_total:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
