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


def get_stable_game_versions() -> Tuple[Optional[List[str]], Optional[str]]:
    """Return list of stable MC version strings, newest first."""
    data, err = fetch_json(f"{FABRIC_META}/versions/game")
    if err:
        return None, f"Failed to fetch game versions: {err}"
    stable = [v["version"] for v in data if v.get("stable")]
    return stable, None


def get_latest_yarn(mc_version: str) -> Tuple[Optional[str], Optional[str]]:
    """Return latest yarn version string (e.g. '1.21.4+build.8') or None."""
    data, err = fetch_json(f"{FABRIC_META}/versions/yarn/{mc_version}")
    if err:
        return None, f"yarn API error: {err}"
    if not data:
        return None, "no yarn mappings published"
    # data is sorted newest-first by the API
    return data[0]["version"], None


def get_latest_loader() -> Tuple[Optional[str], Optional[str]]:
    """Return latest stable loader version string."""
    data, err = fetch_json(f"{FABRIC_META}/versions/loader")
    if err:
        return None, f"loader API error: {err}"
    stable = [l for l in data if l.get("stable")]
    if not stable:
        return None, "no stable loader found"
    return stable[0]["version"], None


def get_latest_fabric_api(mc_version: str, all_fabric_versions: List[str]) -> Tuple[Optional[str], Optional[str]]:
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


def parse_current_matrix(workflow_path: str) -> Tuple[Optional[List[str]], Optional[str]]:
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
            if stripped.startswith("- "):
                # Extract version from: - "1.21.4" or - '1.21.4' or - 1.21.4
                match = re.match(r'-\s*["\']?([^"\'\s]+)["\']?', stripped)
                if match:
                    versions.append(match.group(1))
            elif stripped and not stripped.startswith("#"):
                # End of matrix section
                if not stripped.startswith("-"):
                    break
    return versions, None


def main() -> int:
    """Check for new stable MC versions with Fabric support and report."""
    import argparse

    parser = argparse.ArgumentParser(description="Check for new stable MC versions with Fabric support")
    parser.add_argument(
        "--workflow-file",
        default=DEFAULT_WORKFLOW,
        help="Path to workflow YAML file to check for current matrix",
    )
    args = parser.parse_args()

    result = {
        "current_matrix": [],
        "available_to_add": [],
        "skip_reasons": {},
    }

    # Fetch current matrix
    current, err = parse_current_matrix(args.workflow_file)
    if err:
        print(f"Warning: {err}", file=sys.stderr)
        result["skip_reasons"]["_workflow"] = err
    else:
        result["current_matrix"] = current or []

    # Fetch all stable game versions
    stable_versions, err = get_stable_game_versions()
    if err:
        print(f"Error: {err}", file=sys.stderr)
        result["skip_reasons"]["_game_versions"] = err
        print(json.dumps(result, indent=2))
        return 0

    # Fetch loader version
    loader_version, err = get_latest_loader()
    if err:
        print(f"Warning: {err}", file=sys.stderr)
        result["skip_reasons"]["_loader"] = err

    # Fetch all fabric-api versions
    all_fabric_versions, err = fetch_all_fabric_api_versions()
    if err:
        print(f"Warning: {err}", file=sys.stderr)
        result["skip_reasons"]["_fabric_api"] = err

    # Check each stable version
    for mc_version in stable_versions:
        if mc_version in result["current_matrix"]:
            continue

        # Check yarn
        yarn_version, yarn_err = get_latest_yarn(mc_version)
        if yarn_err:
            result["skip_reasons"][mc_version] = f"yarn: {yarn_err}"
            continue

        # Check fabric-api
        fabric_api_version, fabric_err = get_latest_fabric_api(mc_version, all_fabric_versions or [])
        if fabric_err:
            result["skip_reasons"][mc_version] = f"fabric-api: {fabric_err}"
            continue

        # All checks passed - version is available to add
        result["available_to_add"].append({
            "mc_version": mc_version,
            "yarn": yarn_version,
            "loader": loader_version,
            "fabric_api": fabric_api_version,
        })

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())