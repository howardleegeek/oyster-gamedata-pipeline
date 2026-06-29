#!/usr/bin/env python3
"""
fetch_fabric.py — Download + verify Fabric loader 0.16.10 for MC 1.21.4.

Spec: specs/R05B_bundled_minecraft_packaging.md

Iron-law constraints:
  * Loader version + MC version are PINNED in manifest.json.
  * SHA-1 for every Fabric library comes from the loader's profile JSON
    (Fabric serves SHAs alongside its Maven artifacts) and is RE-VERIFIED
    after download — hard-fail on mismatch (exit 2).
  * stdlib-only — urllib, hashlib, json, pathlib.
  * mainClass anchor: leaf profile must yield
    `net.fabricmc.loader.impl.launch.knot.KnotClient` — refuse to write
    a bundle whose launcher will silently fall through to vanilla Main.
  * Output layout matches what bin/oyster_launch_mc.py expects:
        bundle/mc-instance/
          versions/fabric-loader-0.16.10-1.21.4/
            fabric-loader-0.16.10-1.21.4.json    (leaf profile, verbatim)
          libraries/<group>/<artifact>/<version>/<artifact>-<version>.jar
            (8 Fabric libs at maven-coord paths)

Usage (CI / local build):
    python bin/build_bundled_installer/fetch_fabric.py

Note:
    Run AFTER fetch_minecraft.py — Fabric reuses the vanilla MC libs at
    runtime (Fabric's profile lists ONLY its own incremental libraries,
    not the vanilla ones). This script does not validate the vanilla
    side; it just lays Fabric on top.

Exit codes:
    0  success
    2  SHA-1 mismatch (corrupt download or upstream tampering)
    3  post-fetch verification failed (required files missing, or
       leaf mainClass != KnotClient)
    4  download / network failure after retries
    5  filesystem / IO error
    6  pin manifest invalid / unreadable
    7  Fabric profile JSON invalid (missing libraries / mainClass / etc.)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
PIN_MANIFEST_PATH = SCRIPT_DIR / "manifest.json"

BUNDLE_DIR = REPO_ROOT / "bundle"
MC_INSTANCE_DIR = BUNDLE_DIR / "mc-instance"
CACHE_DIR = REPO_ROOT / "bin" / ".cache" / "fabric"
RUNTIME_MANIFEST_PATH = MC_INSTANCE_DIR / "manifest-fabric.json"

# Network
DOWNLOAD_TIMEOUT_SEC = 120
DOWNLOAD_RETRIES = 3
DOWNLOAD_CHUNK = 1 << 16  # 64 KiB
USER_AGENT = "oyster-agent-runner-bundled-installer/1.0 (+https://meta.fabricmc.net)"

# Fabric Maven base — used to derive download URLs when the profile JSON
# only lists `name` + `url` for a library entry. Fabric's convention is
# `<url><group-as-dirs>/<artifact>/<version>/<artifact>-<version>.jar`.
FABRIC_MAVEN_FALLBACK = "https://maven.fabricmc.net/"
SHA1_URL_SUFFIX = ".sha1"

# Modrinth API for fabric-api (Bug 2 fix). The mod refuses to load without
# fabric-api at runtime ("requires any version of fabric-api, which is
# missing"), so we ship the latest stable build for the pinned MC version.
MODRINTH_FABRIC_API_VERSIONS_URL = (
    "https://api.modrinth.com/v2/project/fabric-api/version"
    "?game_versions=%5B%22{mc_version}%22%5D"
    "&loaders=%5B%22fabric%22%5D"
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(f"[fetch_fabric] {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"[fetch_fabric] ERROR: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()  # noqa: S324 — Fabric's manifest pins SHA-1, not us.
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

    if "fabric_pin" not in full_pin or not isinstance(full_pin["fabric_pin"], dict):
        _err("pin manifest missing required field: fabric_pin")
        sys.exit(6)

    pin = full_pin["fabric_pin"]
    required = [
        "loader_version",
        "minecraft_version",
        "profile_url",
        "expected_main_class",
        "post_fetch_required_files",
    ]
    for key in required:
        if key not in pin:
            _err(f"pin manifest missing required field: fabric_pin.{key}")
            sys.exit(6)
    return pin


# ---------------------------------------------------------------------------
# Download primitives
# ---------------------------------------------------------------------------


def _download_with_retries(url: str, dest: Path) -> int:
    """Download `url` -> `dest` with bounded retries. Returns bytes written."""
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


def _download_text(url: str) -> str:
    """Fetch a small text document with retries (used for .sha1 sidecars)."""
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(  # noqa: S310 — pinned https URL
                req, timeout=DOWNLOAD_TIMEOUT_SEC
            ) as resp:
                return resp.read().decode("utf-8").strip()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < DOWNLOAD_RETRIES:
                backoff = 2 ** (attempt - 1)
                time.sleep(backoff)
    raise RuntimeError(f"text fetch failed after {DOWNLOAD_RETRIES} attempts: {last_exc}")


def _download_json(url: str) -> dict[str, Any]:
    """Fetch a JSON document with retries."""
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
                time.sleep(backoff)
    raise RuntimeError(f"JSON fetch failed after {DOWNLOAD_RETRIES} attempts: {last_exc}")


def _download_json_array(url: str) -> list[dict[str, Any]]:
    """Fetch a JSON array document with retries (Modrinth's response shape)."""
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(  # noqa: S310 — pinned https URL
                req, timeout=DOWNLOAD_TIMEOUT_SEC
            ) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                if not isinstance(payload, list):
                    raise ValueError(f"expected JSON array, got {type(payload).__name__}")
                return payload
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_exc = exc
            if attempt < DOWNLOAD_RETRIES:
                backoff = 2 ** (attempt - 1)
                time.sleep(backoff)
    raise RuntimeError(f"JSON-array fetch failed after {DOWNLOAD_RETRIES} attempts: {last_exc}")


def _fetch_with_sha1_pin(
    url: str,
    dest: Path,
    expected_sha1: str,
    label: str,
) -> int:
    """Download `url` to `dest` and hard-fail on SHA-1 mismatch.

    Reuses cached file iff it already passes the SHA-1 pin.
    Returns bytes downloaded (0 if cache hit).
    """
    if dest.is_file():
        if _sha1_file(dest).lower() == expected_sha1.lower():
            return 0  # cache hit
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

    return bytes_dl


# ---------------------------------------------------------------------------
# Maven helpers
# ---------------------------------------------------------------------------


def _maven_relpath(name: str) -> Path:
    """Convert maven coord 'group:artifact:version' to relpath.

    Same scheme as fetch_minecraft.py — kept here too so each module is
    self-contained (stdlib-only, single-file fetchers).
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


def _maven_url(base_url: str, name: str) -> str:
    """Compute a Maven jar URL given a Maven base + coord."""
    relpath = _maven_relpath(name)
    base = base_url if base_url.endswith("/") else base_url + "/"
    # Use forward slashes — URL paths, not OS paths.
    return base + str(relpath).replace(os.sep, "/")


def _resolve_lib_url_and_sha1(lib: dict[str, Any], default_maven: str) -> tuple[str, str]:
    """Pull a (url, sha1) pair out of a Fabric library entry.

    Fabric's profile JSON describes each library as either:
      A) {"name": "x:y:z", "url": "<maven-base>", "sha1": "..."}     (newer)
      B) {"name": "x:y:z", "url": "<maven-base>"}                    (older;
         SHA1 fetched from the .sha1 sidecar at maven_url + ".sha1")
      C) {"name": "x:y:z", "url": "<maven-base>", "size": ...,
          "sha512": "...", ...}                                      (variants)

    For (B), we follow the Fabric installer's behavior: download the
    `.sha1` sidecar, parse the hex digest. SHA-1 is what Fabric pins.
    """
    name = lib.get("name")
    base = lib.get("url") or default_maven
    if not name:
        raise ValueError(f"library entry missing 'name': {lib!r}")

    jar_url = _maven_url(base, name)

    sha1 = lib.get("sha1")
    if not sha1:
        sha1_url = jar_url + SHA1_URL_SUFFIX
        try:
            sha1_text = _download_text(sha1_url)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"library {name!r}: no sha1 in profile and .sha1 sidecar "
                f"unavailable at {sha1_url} ({exc})"
            ) from exc
        # The sidecar sometimes contains "<hash>  <filename>"; take the first token.
        sha1 = sha1_text.split()[0].strip().lower()
    if len(sha1) != 40:
        raise RuntimeError(f"library {name!r}: sha1 has unexpected length {len(sha1)}: {sha1!r}")
    return jar_url, sha1


# ---------------------------------------------------------------------------
# Profile fetcher
# ---------------------------------------------------------------------------


def _fetch_profile(profile_url: str, loader_version: str, mc_version: str) -> dict[str, Any]:
    """Download the Fabric profile JSON, validate, save to disk, and return it.

    Saves verbatim to:
        bundle/mc-instance/versions/fabric-loader-<loader>-<mc>/<same>.json

    The leaf JSON's `id`, `inheritsFrom`, `mainClass`, and `libraries[]`
    are validated up-front so we can fail-loud before downloading any
    libraries.
    """
    _log(f"fetching Fabric profile: {profile_url}")
    profile = _download_json(profile_url)

    profile_name = f"fabric-loader-{loader_version}-{mc_version}"
    if profile.get("id") != profile_name:
        _err(f"profile id mismatch — expected {profile_name!r}, got {profile.get('id')!r}")
        sys.exit(7)

    if profile.get("inheritsFrom") != mc_version:
        _err(
            f"profile inheritsFrom mismatch — expected {mc_version!r}, "
            f"got {profile.get('inheritsFrom')!r}"
        )
        sys.exit(7)

    if not profile.get("mainClass"):
        _err("profile missing mainClass")
        sys.exit(7)

    libs = profile.get("libraries")
    if not isinstance(libs, list) or not libs:
        _err("profile has empty / missing libraries[]")
        sys.exit(7)

    # Save verbatim under bundle/mc-instance/versions/<profile_name>/.
    versions_dir = MC_INSTANCE_DIR / "versions" / profile_name
    versions_dir.mkdir(parents=True, exist_ok=True)
    json_path = versions_dir / f"{profile_name}.json"
    json_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return profile


# ---------------------------------------------------------------------------
# Library fetcher
# ---------------------------------------------------------------------------


def _fetch_libraries(profile: dict[str, Any]) -> tuple[int, int, list[dict[str, str]]]:
    """Download every library declared by the Fabric profile.

    Returns (bytes_downloaded, libraries_kept, library_records).
    `library_records` lists each {"name", "path", "sha1"} for the runtime
    manifest.
    """
    libs_root = MC_INSTANCE_DIR / "libraries"
    libs_root.mkdir(parents=True, exist_ok=True)

    bytes_dl = 0
    kept = 0
    records: list[dict[str, str]] = []

    for lib in profile["libraries"]:
        name = lib["name"]
        rel = _maven_relpath(name)
        dest = libs_root / rel
        cache = CACHE_DIR / "libs" / rel

        try:
            url, sha1 = _resolve_lib_url_and_sha1(lib, FABRIC_MAVEN_FALLBACK)
        except Exception as exc:  # noqa: BLE001
            _err(f"could not resolve library {name!r}: {exc}")
            sys.exit(7)

        bytes_dl += _fetch_with_sha1_pin(
            url=url, dest=cache, expected_sha1=sha1, label=f"fabric lib {name}"
        )
        if not dest.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(cache.read_bytes())
        kept += 1
        records.append({"name": name, "path": str(rel).replace(os.sep, "/"), "sha1": sha1})

    return bytes_dl, kept, records


# ---------------------------------------------------------------------------
# fabric-api fetcher (Bug 2 fix)
# ---------------------------------------------------------------------------


def _pick_latest_stable_release(
    versions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pick the latest stable release from a Modrinth versions array.

    Modrinth returns versions sorted newest-first, so the first element
    with ``version_type == "release"`` is "latest stable". We never
    accept ``alpha`` or ``beta`` builds — the consumer installer must
    always ship a release build.
    """
    for v in versions:
        if v.get("version_type") == "release":
            return v
    raise RuntimeError(
        "no stable release found in Modrinth fabric-api versions; "
        "refusing to ship an alpha/beta build to consumers"
    )


def _pick_primary_file(version: dict[str, Any]) -> dict[str, Any]:
    """Return the version's primary file entry (the .jar end-users install)."""
    files = version.get("files") or []
    for f in files:
        if f.get("primary"):
            return f
    raise RuntimeError(f"Modrinth version {version.get('version_number')!r} has no primary file")


def _fetch_fabric_api(mc_version: str) -> tuple[dict[str, Any], int]:
    """Download fabric-api for ``mc_version`` from Modrinth.

    Bug 2 fix: without fabric-api, the recorder mod fails to load with::

        requires any version of fabric-api, which is missing

    Saves to ``bundle/mc-instance/mods/fabric-api.jar`` (stable filename
    so downstream tooling doesn't have to know which version we shipped).

    Returns a (record, bytes_downloaded) pair where ``record`` is a
    runtime-manifest entry (version_number, sha1, size, source url, ...).
    """
    mods_dir = MC_INSTANCE_DIR / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    dest = mods_dir / "fabric-api.jar"

    # Discover latest stable
    api_url = MODRINTH_FABRIC_API_VERSIONS_URL.format(mc_version=mc_version)
    _log(f"querying Modrinth fabric-api for MC {mc_version}: {api_url}")
    versions = _download_json_array(api_url)
    if not versions:
        _err(f"Modrinth returned 0 fabric-api versions for MC {mc_version}")
        sys.exit(7)

    chosen = _pick_latest_stable_release(versions)
    primary = _pick_primary_file(chosen)
    version_number = chosen.get("version_number") or "(unknown)"
    url = primary.get("url")
    sha1 = (primary.get("hashes") or {}).get("sha1")
    if not url or not sha1:
        _err(
            f"fabric-api {version_number}: Modrinth primary file missing "
            f"url or sha1 (url={url!r}, sha1={sha1!r})"
        )
        sys.exit(7)

    _log(f"fabric-api: chose {version_number} -> {primary.get('filename')}")
    cache = CACHE_DIR / "mods" / f"fabric-api-{version_number}.jar"
    bytes_dl = _fetch_with_sha1_pin(
        url=url,
        dest=cache,
        expected_sha1=sha1,
        label=f"fabric-api {version_number}",
    )
    # Place at stable name so installer doesn't need to know version.
    if not dest.is_file() or _sha1_file(dest).lower() != sha1.lower():
        dest.write_bytes(cache.read_bytes())

    record = {
        "name": "fabric-api",
        "minecraft_version": mc_version,
        "version_number": version_number,
        "filename": primary.get("filename"),
        "size": int(primary.get("size") or 0),
        "sha1": sha1,
        "url": url,
        "modrinth_version_id": chosen.get("id"),
    }
    return record, bytes_dl


# ---------------------------------------------------------------------------
# Verification + manifest
# ---------------------------------------------------------------------------


def _verify_post_fetch(
    profile: dict[str, Any],
    expected_main: str,
    required_relpaths: list[str],
) -> None:
    """Verify (1) leaf mainClass anchor and (2) required file paths."""
    actual_main = profile.get("mainClass")
    if actual_main != expected_main:
        _err(
            f"leaf mainClass mismatch — expected {expected_main!r}, "
            f"got {actual_main!r}. The launcher would fall through to "
            f"vanilla Main and silently break the recorder mod."
        )
        sys.exit(3)

    missing: list[str] = []
    for rel in required_relpaths:
        if not (MC_INSTANCE_DIR / rel).is_file():
            missing.append(rel)
    if missing:
        _err(f"required Fabric artifacts missing: {missing}")
        sys.exit(3)
    _log(
        f"post-fetch verification OK "
        f"({len(required_relpaths)} required files, "
        f"mainClass={actual_main!r})"
    )


def _dir_size_bytes(root: Path) -> int:
    total = 0
    for dp, _dn, fn in os.walk(root):
        for name in fn:
            with suppress(OSError):
                total += (Path(dp) / name).stat().st_size
    return total


def _write_runtime_manifest(
    pin: dict[str, Any],
    profile: dict[str, Any],
    libraries: list[dict[str, str]],
    bytes_downloaded: int,
    fabric_api: dict[str, Any] | None = None,
) -> None:
    """Emit bundle/mc-instance/manifest-fabric.json — runtime provenance record."""
    manifest = {
        "schema_version": 1,
        "produced_by": "bin/build_bundled_installer/fetch_fabric.py",
        "produced_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pin": {
            "vendor": pin.get("vendor", "fabricmc"),
            "loader_version": pin["loader_version"],
            "minecraft_version": pin["minecraft_version"],
            "profile_url": pin["profile_url"],
            "expected_main_class": pin["expected_main_class"],
        },
        "upstream": {
            "profile_id": profile.get("id"),
            "inherits_from": profile.get("inheritsFrom"),
            "main_class": profile.get("mainClass"),
            "release_time": profile.get("releaseTime"),
            "library_count": len(libraries),
        },
        "libraries": libraries,
        "fabric_api": fabric_api,
        "result": {
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
    loader_version: str = pin["loader_version"]
    mc_version: str = pin["minecraft_version"]
    profile_url: str = pin["profile_url"]
    expected_main: str = pin["expected_main_class"]
    expected_count: int = int(pin.get("expected_libraries_count") or 0)
    required_files: list[str] = list(pin["post_fetch_required_files"])

    _log(f"target: Fabric loader {loader_version} for MC {mc_version}")
    _log(f"  profile  = {profile_url}")
    _log(f"  expected mainClass = {expected_main}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MC_INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

    # Stage 1: profile JSON.
    try:
        profile = _fetch_profile(profile_url, loader_version, mc_version)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        _err(f"failed to fetch / parse profile: {exc}")
        return 4

    actual_count = len(profile.get("libraries", []))
    if expected_count and actual_count != expected_count:
        _log(
            f"  warning: profile lists {actual_count} libraries; "
            f"manifest pin expected {expected_count}. Continuing — "
            f"Fabric occasionally rebalances the dependency set."
        )

    # Stage 2: libraries.
    _log(f"fetching {actual_count} Fabric libraries")
    bytes_dl, libs_kept, library_records = _fetch_libraries(profile)
    _log(f"  libraries: {libs_kept} kept ({bytes_dl:,} bytes downloaded this run)")

    # Stage 3: fabric-api (Bug 2 fix).
    _log("fetching fabric-api from Modrinth")
    fabric_api_record, fabric_api_bytes = _fetch_fabric_api(mc_version)
    bytes_dl += fabric_api_bytes
    _log(
        f"  fabric-api: {fabric_api_record['version_number']} "
        f"({fabric_api_bytes:,} bytes downloaded this run)"
    )

    # Verify + write runtime manifest.
    _verify_post_fetch(profile, expected_main, required_files)
    _write_runtime_manifest(pin, profile, library_records, bytes_dl, fabric_api=fabric_api_record)

    instance_size = _dir_size_bytes(MC_INSTANCE_DIR)
    _log(
        f"done. bundle/mc-instance/ = {instance_size:,} bytes "
        f"({instance_size / (1 << 20):.1f} MiB) after Fabric overlay"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
