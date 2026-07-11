#!/usr/bin/env python3
"""Generate the static Minecraft Java @argfile template at build time.

Runtime launch_mc.bat deliberately does no JSON parsing. This script runs
after fetch_minecraft.py and fetch_fabric.py have staged bundle/mc-instance.
It reads the Fabric/Minecraft profile JSON once, resolves the classpath, and
writes mc_args_template.txt with literal {ROOT} tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
PIN_MANIFEST = SCRIPT_DIR / "manifest.json"
DEFAULT_INSTANCE_DIR = REPO_ROOT / "bundle" / "mc-instance"
DEFAULT_OUTPUT = SCRIPT_DIR / "mc_args_template.txt"
EXPECTED_MAIN_CLASS = "net.fabricmc.loader.impl.launch.knot.KnotClient"
FABRIC_MC_EMU_ARG = '"-DFabricMcEmu= net.minecraft.client.main.Main"'


def _err(message: str) -> None:
    print(f"gen_mc_args_template.py: error: {message}", file=sys.stderr)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"missing JSON file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _maven_relpath(name: str) -> Path:
    parts = name.split(":")
    if len(parts) < 3:
        raise ValueError(f"bad Maven coordinate: {name!r}")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = f"-{parts[3]}" if len(parts) >= 4 else ""
    return Path(*group.split("."), artifact, version, f"{artifact}-{version}{classifier}.jar")


def _evaluate_rules(rules: list[dict[str, Any]] | None, target_os: str = "windows") -> bool:
    if not rules:
        return True

    allowed = False
    for rule in rules:
        action = rule.get("action", "allow")
        os_clause = rule.get("os")
        applies = True
        if isinstance(os_clause, dict):
            os_name = os_clause.get("name")
            if os_name is not None and os_name != target_os:
                applies = False
        if applies:
            allowed = action == "allow"
    return allowed


def _is_native_library(lib: dict[str, Any]) -> bool:
    name = lib.get("name")
    if not isinstance(name, str):
        return False
    parts = name.split(":")
    classifier = parts[3] if len(parts) >= 4 else ""
    if classifier.startswith("natives-"):
        return True
    if lib.get("natives"):
        return True
    artifact = ((lib.get("downloads") or {}).get("artifact") or {}).get("path", "")
    return "natives-" in str(artifact)


def _library_relpath(lib: dict[str, Any]) -> Path:
    artifact = (lib.get("downloads") or {}).get("artifact") or {}
    path = artifact.get("path")
    if isinstance(path, str) and path:
        return Path(path)
    name = lib.get("name")
    if not isinstance(name, str):
        raise ValueError(f"library entry has no Maven name: {lib!r}")
    return _maven_relpath(name)


def _resolve_classpath(
    *,
    instance_dir: Path,
    leaf: dict[str, Any],
    parent: dict[str, Any],
    mc_version: str,
    root_token: str,
) -> str:
    libraries_root = instance_dir / "libraries"
    seen: set[str] = set()
    entries: list[str] = []
    missing: list[str] = []

    for lib in list(leaf.get("libraries") or []) + list(parent.get("libraries") or []):
        name = lib.get("name")
        if not isinstance(name, str):
            continue
        if not _evaluate_rules(lib.get("rules")):
            continue
        if _is_native_library(lib):
            continue

        key = ":".join(name.split(":")[:2])
        if key in seen:
            continue
        seen.add(key)

        rel = _library_relpath(lib)
        jar = libraries_root / rel
        if not jar.is_file():
            missing.append(str(rel).replace("\\", "/"))
            continue
        entries.append(f"{root_token}/mc-instance/libraries/{rel.as_posix()}")

    client_jar = instance_dir / "versions" / mc_version / f"{mc_version}.jar"
    if not client_jar.is_file():
        missing.append(f"versions/{mc_version}/{mc_version}.jar")
    else:
        entries.append(f"{root_token}/mc-instance/versions/{mc_version}/{mc_version}.jar")

    if missing:
        raise FileNotFoundError(
            "classpath input jar(s) missing from staged mc-instance: " + ", ".join(missing)
        )
    if not entries:
        raise RuntimeError("resolved empty Minecraft classpath")
    return ";".join(entries)


def _offline_uuid(username: str) -> str:
    return str(uuid.uuid3(uuid.NAMESPACE_DNS, f"OfflinePlayer:{username}"))


def generate_template(
    *,
    instance_dir: Path,
    output: Path,
    manifest_path: Path,
    root_token: str,
    username: str,
) -> list[str]:
    manifest = _load_json(manifest_path)
    mc_pin = manifest.get("mc_pin") or {}
    fabric_pin = manifest.get("fabric_pin") or {}
    mc_version = str(mc_pin.get("version_id") or fabric_pin.get("minecraft_version") or "1.21.4")
    loader_version = str(fabric_pin.get("loader_version") or "0.16.10")
    profile_name = f"fabric-loader-{loader_version}-{mc_version}"

    leaf_path = instance_dir / "versions" / profile_name / f"{profile_name}.json"
    parent_path = instance_dir / "versions" / mc_version / f"{mc_version}.json"
    leaf = _load_json(leaf_path)
    parent = _load_json(parent_path)

    main_class = str(leaf.get("mainClass") or "")
    expected_main = str(fabric_pin.get("expected_main_class") or EXPECTED_MAIN_CLASS)
    if main_class != expected_main:
        raise RuntimeError(
            f"Fabric mainClass mismatch: got {main_class!r}, expected {expected_main!r}"
        )

    asset_index = str(
        (parent.get("assetIndex") or {}).get("id")
    ) or mc_pin.get("asset_index_id") or "19"
    classpath = _resolve_classpath(
        instance_dir=instance_dir,
        leaf=leaf,
        parent=parent,
        mc_version=mc_version,
        root_token=root_token,
    )

    natives = f"{root_token}/mc-instance/versions/{mc_version}/natives"
    instance = f"{root_token}/mc-instance"
    lines = [
        "-Xmx4G",
        "-Xms4G",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:+UseG1GC",
        "-XX:G1NewSizePercent=20",
        "-XX:G1ReservePercent=20",
        "-XX:MaxGCPauseMillis=50",
        "-XX:G1HeapRegionSize=32M",
        FABRIC_MC_EMU_ARG,
        f"-Djava.library.path={natives}",
        "-cp",
        classpath,
        main_class,
        "--username",
        username,
        "--version",
        profile_name,
        "--gameDir",
        instance,
        "--assetsDir",
        f"{instance}/assets",
        "--assetIndex",
        asset_index,
        "--uuid",
        _offline_uuid(username),
        "--accessToken",
        "0",
        '--clientId ""',
        '--xuid ""',
        "--userType",
        "legacy",
        "--versionType",
        "release",
    ]

    if len(lines) != 33:
        raise RuntimeError(f"template line count changed: expected 33, got {len(lines)}")
    if any("\\" in line for line in lines):
        raise RuntimeError("template contains a backslash; paths must use forward slashes")
    if root_token not in "\n".join(lines):
        raise RuntimeError(f"template missing {root_token} token")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-dir", type=Path, default=DEFAULT_INSTANCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=PIN_MANIFEST)
    parser.add_argument("--root-token", default="{ROOT}")
    parser.add_argument("--username", default="Player")
    args = parser.parse_args()

    try:
        lines = generate_template(
            instance_dir=args.instance_dir,
            output=args.output,
            manifest_path=args.manifest,
            root_token=args.root_token,
            username=args.username,
        )
    except Exception as exc:  # noqa: BLE001
        _err(str(exc))
        return 1

    print(f"wrote {args.output} ({len(lines)} lines)")
    print(f"classpath jars: {lines[11].count(';') + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
