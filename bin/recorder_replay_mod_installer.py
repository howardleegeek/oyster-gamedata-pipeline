#!/usr/bin/env python3
"""
bin/recorder_replay_mod_installer.py — On-demand Replay Mod installer.

Per ``docs/RESEARCH_DEPTH_CAPTURE_MC.md``, the **Replay Mod** is the only
known way to capture true GPU-resident depth EXR files from a vanilla
Minecraft Java client (matching the buyer-spec depth quality requirement).
The mod requires a per-Minecraft-version build, so this installer:

  1.  Detects the Minecraft version from the player's launcher profile or
      ``--mc-version`` flag.
  2.  Resolves the matching Replay Mod release jar via the
      `replaymod.com/api/v1/files` JSON endpoint (or a configurable
      mirror).
  3.  Downloads it (with SHA-256 verification when an expected hash is
      provided) into ``%APPDATA%/.minecraft/mods/`` (Windows),
      ``$HOME/Library/Application Support/minecraft/mods/`` (macOS), or
      ``$HOME/.minecraft/mods/`` (Linux).
  4.  Writes ``docs/REPLAY_MOD_USAGE.md`` with the operator instructions
      (key bindings, GPU depth EXR export, frame-cadence settings).

This is a **NEW FILE**.  ``bin/recorder_consumer_lite.py`` is **not edited**;
it (or a future installer GUI) can shell out to this script as needed.

The installer is offline-safe: in ``--dry-run`` mode it resolves URLs and
prints planned actions without writing the network or the .minecraft folder.

Spec: G264 (W31 wave). PP1 priority. ~200 lines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Default Replay Mod release index URL.  This endpoint returns a JSON list of
#: releases keyed by Minecraft version.  Override via ``--index-url`` when
#: testing or when fronting through a mirror.
DEFAULT_INDEX_URL: str = "https://www.replaymod.com/api/v1/files"

#: Filename of the operator usage doc this installer emits.
USAGE_DOC_PATH: str = "docs/REPLAY_MOD_USAGE.md"

#: Filename pattern of the downloaded Replay Mod jar.
JAR_NAME_TEMPLATE: str = "replaymod-{mc_version}-{mod_version}.jar"


@dataclass(frozen=True)
class ReplayModRelease:
    """Single Replay Mod release record."""

    mc_version: str
    mod_version: str
    download_url: str
    sha256: Optional[str] = None


def detect_minecraft_dir() -> Path:
    """Return the platform-canonical ``.minecraft`` directory path.

    Returns:
        Path object (may not exist yet).
    """
    sysname = platform.system()
    if sysname == "Windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / ".minecraft"
    if sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "minecraft"
    return Path.home() / ".minecraft"


def detect_minecraft_version(mc_dir: Optional[Path] = None) -> Optional[str]:
    """Best-effort detect the most-recently-launched Minecraft version.

    Reads ``launcher_profiles.json`` and returns the ``lastVersionId`` of the
    most recently used profile.  Returns ``None`` when the file is missing
    or unparseable — the caller must then provide ``--mc-version``.

    Args:
        mc_dir: Override the .minecraft directory path.

    Returns:
        Detected version string (e.g. ``"1.20.1"``) or None.
    """
    mc_dir = mc_dir or detect_minecraft_dir()
    profiles = mc_dir / "launcher_profiles.json"
    if not profiles.is_file():
        return None
    try:
        data = json.loads(profiles.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    profs = data.get("profiles") or {}
    if not isinstance(profs, dict):
        return None
    best_ts = ""
    best_ver: Optional[str] = None
    for prof in profs.values():
        if not isinstance(prof, dict):
            continue
        ts = str(prof.get("lastUsed") or prof.get("created") or "")
        ver = prof.get("lastVersionId")
        if isinstance(ver, str) and ts >= best_ts:
            best_ts = ts
            best_ver = ver
    return best_ver


def fetch_release_index(index_url: str = DEFAULT_INDEX_URL, timeout: int = 30) -> list:
    """Fetch the Replay Mod release index JSON.

    Args:
        index_url: Override URL.
        timeout: HTTP timeout in seconds.

    Returns:
        Raw decoded JSON (expected list of release dicts).

    Raises:
        urllib.error.URLError: Network failure.
        ValueError: Response is not valid JSON.
    """
    req = urllib.request.Request(
        index_url, headers={"User-Agent": "oyster-recorder-replay-mod-installer/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted)
        body = resp.read().decode("utf-8")
    return json.loads(body)


def resolve_release(
    mc_version: str,
    index: list,
) -> ReplayModRelease:
    """Pick the highest-mod-version release matching ``mc_version``.

    Args:
        mc_version: Target Minecraft version string.
        index: Decoded release index list (entries are dicts with at least
            ``mc_version``, ``mod_version``, ``url`` keys).

    Returns:
        :class:`ReplayModRelease`.

    Raises:
        LookupError: No release matches the target version.
    """
    candidates = [r for r in index if isinstance(r, dict) and r.get("mc_version") == mc_version]
    if not candidates:
        raise LookupError(f"no Replay Mod release for Minecraft {mc_version}")
    candidates.sort(key=lambda r: str(r.get("mod_version", "")))
    best = candidates[-1]
    return ReplayModRelease(
        mc_version=mc_version,
        mod_version=str(best["mod_version"]),
        download_url=str(best["url"]),
        sha256=(str(best["sha256"]) if best.get("sha256") else None),
    )


def download_jar(
    release: ReplayModRelease,
    mods_dir: Path,
    timeout: int = 120,
    chunk_size: int = 65536,
) -> Path:
    """Download the release jar into ``mods_dir`` and return its path.

    If ``release.sha256`` is set, verifies the digest after download and
    deletes the file on mismatch.

    Args:
        release: Release record from :func:`resolve_release`.
        mods_dir: Destination ``.minecraft/mods/`` path.
        timeout: HTTP timeout.
        chunk_size: Streaming chunk size in bytes.

    Returns:
        Path to the downloaded jar.

    Raises:
        urllib.error.URLError: Network failure.
        ValueError: SHA-256 mismatch.
    """
    mods_dir.mkdir(parents=True, exist_ok=True)
    jar_name = JAR_NAME_TEMPLATE.format(
        mc_version=release.mc_version, mod_version=release.mod_version
    )
    out = mods_dir / jar_name
    sha = hashlib.sha256()
    req = urllib.request.Request(
        release.download_url,
        headers={"User-Agent": "oyster-recorder-replay-mod-installer/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted)
        with out.open("wb") as fh:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                fh.write(chunk)
                sha.update(chunk)

    if release.sha256 and sha.hexdigest() != release.sha256:
        out.unlink(missing_ok=True)
        raise ValueError(
            f"sha256 mismatch: expected {release.sha256}, got {sha.hexdigest()}"
        )
    return out


def _format_jar_name(release: ReplayModRelease) -> str:
    """Return the canonical Replay Mod jar filename for ``release``."""
    return JAR_NAME_TEMPLATE.format(
        mc_version=release.mc_version,
        mod_version=release.mod_version,
    )


def write_usage_doc(
    repo_root: Path,
    release: ReplayModRelease,
    mods_dir: Path,
) -> Path:
    """Emit ``docs/REPLAY_MOD_USAGE.md`` describing operator instructions.

    Args:
        repo_root: Repository root (so ``docs/`` resolves correctly).
        release: Release that was installed (or planned).
        mods_dir: Mods directory the jar was placed into.

    Returns:
        Path to the written doc.
    """
    doc_path = repo_root / USAGE_DOC_PATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Replay Mod — Recorder Operator Guide\n\n"
        f"_Auto-generated by `bin/recorder_replay_mod_installer.py` for Minecraft "
        f"`{release.mc_version}` + Replay Mod `{release.mod_version}`._\n\n"
        "## Why this mod\n\n"
        "Replay Mod is the only known mechanism that exposes Minecraft Java's GPU\n"
        "depth buffer in a form we can export to OpenEXR for the buyer-spec\n"
        "depth track. See `docs/RESEARCH_DEPTH_CAPTURE_MC.md` for the research\n"
        "trail.\n\n"
        "## Installed jar\n\n"
        f"- Mods directory: `{mods_dir}`\n"
        f"- Jar filename: `{_format_jar_name(release)}`\n"
        f"- Download URL: {release.download_url}\n\n"
        "## In-game workflow\n\n"
        "1. Launch Minecraft with the matching version profile.\n"
        "2. Press `Insert` → opens Replay Mod main menu.\n"
        "3. Click `Render Settings` → set frame cadence to **6 fps**.\n"
        "4. Click `Export → Depth EXR`.\n"
        "5. Copy the produced `.exr` files into the recorder clip's\n"
        "   `depth/` directory before running `bin/recorder_manifest.py`.\n"
    )
    doc_path.write_text(body, encoding="utf-8")
    return doc_path


def install(
    repo_root: Path,
    mc_version: Optional[str] = None,
    index_url: str = DEFAULT_INDEX_URL,
    mods_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Top-level orchestrator: detect → resolve → download → emit doc.

    Args:
        repo_root: Repository root (for ``docs/REPLAY_MOD_USAGE.md``).
        mc_version: Override detection.
        index_url: Override Replay Mod index URL.
        mods_dir: Override .minecraft/mods/ destination.
        dry_run: If True, do NOT hit the network or write the jar; only
            write the usage doc.

    Returns:
        Summary dict suitable for JSON output.
    """
    detected = mc_version or detect_minecraft_version()
    if detected is None:
        raise LookupError(
            "could not detect Minecraft version — pass --mc-version explicitly"
        )

    mods_dir = mods_dir or (detect_minecraft_dir() / "mods")
    if dry_run:
        release = ReplayModRelease(
            mc_version=detected,
            mod_version="(dry-run)",
            download_url=index_url,
        )
        doc = write_usage_doc(repo_root, release, mods_dir)
        return {
            "dry_run": True,
            "mc_version": detected,
            "mods_dir": str(mods_dir),
            "usage_doc": str(doc),
        }

    index = fetch_release_index(index_url)
    release = resolve_release(detected, index)
    jar = download_jar(release, mods_dir)
    doc = write_usage_doc(repo_root, release, mods_dir)
    return {
        "dry_run": False,
        "mc_version": detected,
        "mod_version": release.mod_version,
        "jar_path": str(jar),
        "mods_dir": str(mods_dir),
        "usage_doc": str(doc),
    }


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="On-demand Replay Mod jar installer + usage doc emitter."
    )
    parser.add_argument("--mc-version", help="Override detected Minecraft version")
    parser.add_argument("--index-url", default=DEFAULT_INDEX_URL)
    parser.add_argument("--mods-dir", help="Override .minecraft/mods/ destination")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Repository root (default: parent of bin/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Skip network / file writes (doc only)"
    )
    args = parser.parse_args(argv)

    try:
        summary = install(
            repo_root=Path(args.repo_root),
            mc_version=args.mc_version,
            index_url=args.index_url,
            mods_dir=Path(args.mods_dir) if args.mods_dir else None,
            dry_run=args.dry_run,
        )
    except (LookupError, ValueError, urllib.error.URLError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
