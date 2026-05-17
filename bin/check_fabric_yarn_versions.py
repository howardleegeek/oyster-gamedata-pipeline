#!/usr/bin/env python3
"""
R02 Fabric-Yarn Watcher — checks which stable MC versions can be added to the
build matrix based on real upstream data from:
  - meta.fabricmc.net  (game versions, yarn mappings, loader)
  - maven.fabricmc.net (fabric-api artifacts)

Iron law: NEVER invent coords. Every version reported as addable is backed by
a real API response. If either yarn or fabric-api is missing, skip with a
logged reason.

Usage:
    python3 bin/check_fabric_yarn_versions.py
    python3 bin/check_fabric_yarn_versions.py --workflow-file .github/workflows/build-mc-mod.yml

Output (stdout): JSON with current matrix, available_to_add, and skip reasons.
Exit code: always 0 (report-only, never crashes cron).
"""

import json
import re
import sys
import urllib.request
import urllib.error
from typing import List, Optional, Tuple, Any


FABRIC_META = "https://meta.fabricmc.net/v2"
FABRIC_MAVEN = "https://maven.fabricmc.net"
FABRIC_API_MAVEN_METADATA = (
    f"{FABRIC_MAVEN}/net/fabricmc/fabric-api/fabric-api/maven-metadata.xml"
)
DEFAULT_WORKFLOW = ".github/workflows/build-mc-mod.yml"


def fetch_json(url: str) -> Tuple[Optional[Any], Optional[str]]:
    """Fetch JSON from URL. Returns (data, None) or (None, error_string)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "oyster-fabric-watcher/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()), None
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        return None, str(e)


def fetch_text(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Fetch raw text from URL. Returns (text, None) or (None, error_string)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "oyster-fabric-watcher/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8"), None
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        return None, str(e)


def get_stable_game_versions():
    """Return list of stable MC version strings, newest first."""
    data, err = fetch_json(f"{FABRIC_META}/versions/game")
    if err:
        return None, f"Failed to fetch game versions: {err}"
    stable = [v["version"] for v in data if v.get("stable")]
    return stable, None


def get_latest_yarn(mc_version):
    """Return latest yarn version string (e.g. '1.21.4+build.8') or None."""
    data, err = fetch_json(f"{FABRIC_META}/versions/yarn/{mc_version}")
    if err:
        return None, f"yarn API error: {err}"
    if not data:
        return None, "no yarn mappings published"
    # data is sorted newest-first by the API
    return data[0]["version"], None


def get_latest_loader():
    """Return latest stable loader version string."""
    data, err = fetch_json(f"{FABRIC_META}/versions/loader")
    if err:
        return None, f"loader API error: {err}"
    stable = [l for l in data if l.get("stable")]
    if not stable:
        return None, "no stable loader found"
    return stable[0]["version"], None


def get_latest_fabric_api(mc_version, all_fabric_versions):
    """Return latest fabric-api version for a given MC version, or None."""
    suffix = f"+{mc_version}"
    matches = [v for v in all_fabric_versions if v.endswith(suffix)]
    if not matches:
        return None, f"no fabric-api artifact with suffix '{suffix}'"
    # Maven metadata lists versions in publication order; last = latest
    return matches[-1], None


def fetch_all_fabric_api_versions() -> Tuple[Optional[List[str]], Optional[str]]:
    """Parse maven-metadata.xml and return list of all version strings."""
    xml, err = fetch_text(FABRIC_API_MAVEN_METADATA)
    if err:
        return None, f"Failed to fetch fabric-api maven-metadata.xml: {err}"
    versions = re.findall(r"<version>([^<]+)</version>", xml)
    return versions, None


def parse_current_matrix(workflow_path):
    """Extract current mc_version list from workflow YAML."""
    try:
        with open(workflow_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        return None, f"Workflow file not found: {workflow_path}"

    # Parse the mc_version list from the YAML matrix
    # Look for lines like:  - "1.21.4"
    in_matrix = False
    versions = []
    for line in content.splitlines():
        stripped = line.strip()
        if "mc_version:" in stripped:
            in_matrix = True
            continue
        if in_matrix:
            m = re.match(r'^-\s*["\']([^"\']+)["\']', stripped)
            if m:
                versions.append(m.group(1))
            elif stripped.startswith("-"):
                # Bare value without quotes
                val = stripped.lstrip("- ").strip()
                if re.match(r"^\d+\.\d+", val):
                    versions.append(val)
            else:
                # End of mc_version list
                break
    return versions, None


def determine_java_release(mc_version: str) -> int:
    """Heuristic for Java release based on MC version.

    MC < 1.20.5 requires Java 17. MC 1.20.5+ requires Java 21.
    MC 26.x+ uses Java 21.
    """
    parts = mc_version.split(".")
    if not parts:
        return 21

    try:
        major = int(parts[0])
    except ValueError:
        return 21

    if major >= 26:
        return 21

    if major == 1:
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        # 1.20.4 and below → Java 17; 1.20.5+ → Java 21
        if minor < 20:
            return 17
        if minor == 20 and patch <= 4:
            return 17
        return 21

    return 21


def main():
    workflow_path = DEFAULT_WORKFLOW
    if len(sys.argv) > 2 and sys.argv[1] == "--workflow-file":
        workflow_path = sys.argv[2]

    errors = []

    # 1. Parse current matrix
    current_versions, err = parse_current_matrix(workflow_path)
    if err:
        errors.append(err)
        current_versions = []

    # 2. Fetch stable game versions from Fabric meta
    stable_versions, err = get_stable_game_versions()
    if err:
        errors.append(err)
        print(json.dumps({
            "current": current_versions,
            "available_to_add": [],
            "skip_reason": {},
            "errors": errors,
        }, indent=2))
        return

    # 3. Fetch fabric-api Maven versions (one call, reuse for all checks)
    all_fabric_api, err = fetch_all_fabric_api_versions()
    if err:
        errors.append(err)
        print(json.dumps({
            "current": current_versions,
            "available_to_add": [],
            "skip_reason": {},
            "errors": errors,
        }, indent=2))
        return

    # 4. Fetch latest stable loader
    latest_loader, err = get_latest_loader()
    if err:
        errors.append(f"loader warning: {err}")
        latest_loader = "0.16.14"  # fallback to known-good

    # 5. Check each stable version not in current matrix
    candidates = [v for v in stable_versions if v not in current_versions]
    available_to_add = []
    skip_reason = {}

    for mc_ver in candidates:
        # Gate 1: yarn mappings
        yarn_ver, yarn_err = get_latest_yarn(mc_ver)
        if yarn_err:
            skip_reason[mc_ver] = f"yarn: {yarn_err}"
            continue

        # Gate 2: fabric-api artifact
        fapi_ver, fapi_err = get_latest_fabric_api(mc_ver, all_fabric_api)
        if fapi_err:
            skip_reason[mc_ver] = f"fabric-api: {fapi_err}"
            continue

        # Both gates passed
        java_rel = determine_java_release(mc_ver)
        available_to_add.append({
            "mc_version": mc_ver,
            "yarn": yarn_ver,
            "fabric_api": fapi_ver,
            "loader": latest_loader,
            "java_release": java_rel,
        })

    result = {
        "current": current_versions,
        "available_to_add": available_to_add,
        "skip_reason": skip_reason,
        "errors": errors,
        "latest_loader": latest_loader,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()