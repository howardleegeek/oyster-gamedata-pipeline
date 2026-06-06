#!/usr/bin/env bash
# verify_latest_release_assets.sh — smoke-test the public latest release.
#
# Usage:
#   bash scripts/verify_latest_release_assets.sh [tag]
#
# If tag is omitted, the script checks the repository's latest release.

set -euo pipefail

log() {
  echo "[release-smoke] $*"
}

die() {
  echo "[release-smoke] ERROR: $*" >&2
  exit 1
}

if ! command -v gh >/dev/null 2>&1; then
  die "gh CLI is required"
fi

if ! command -v curl >/dev/null 2>&1; then
  die "curl is required"
fi

gh_release() {
  if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    gh release "$@" -R "$GITHUB_REPOSITORY"
  else
    gh release "$@"
  fi
}

requested_tag="${1:-}"
tag="$requested_tag"
if [ -z "$tag" ]; then
  # Channel-aware: resolve the newest CONSUMER-channel release, ignoring
  # bundled/runtime tags (e.g. recorder-v2.6.x) even when GitHub marks them
  # "Latest". All channel logic lives in the unit-tested latest_consumer_release.
  tag=$(
    GH_REPO="${GITHUB_REPOSITORY:-}" python3 - <<'PY'
import json
import os
import subprocess
import sys

sys.path.insert(0, "src")
from oyster_agent_runner.release_channels import (
    ReleaseInfo,
    latest_consumer_release,
)

repo = os.environ.get("GH_REPO") or subprocess.check_output(
    ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
    text=True,
).strip()
raw = subprocess.check_output(
    ["gh", "api", f"repos/{repo}/releases?per_page=100"], text=True
)
releases = [
    ReleaseInfo(
        tag=item["tag_name"],
        published_at=item.get("published_at") or "",
        asset_names=tuple(asset["name"] for asset in item.get("assets", [])),
    )
    for item in json.loads(raw)
]
resolved = latest_consumer_release(releases)
if resolved is None:
    sys.exit("no consumer-channel release found")
print(resolved.tag)
PY
  )
fi

if [ -z "$tag" ]; then
  die "No release tag found"
fi

log "Checking release ${tag}"

if [ -z "$requested_tag" ]; then
  source_anchor=$(
    python3 - <<'PY'
from pathlib import Path
import ast

tree = ast.parse(Path("src/oyster_agent_runner/release_channels.py").read_text())
for node in tree.body:
    if (
        isinstance(node, ast.Assign)
        and any(getattr(target, "id", None) == "CURRENT_CONSUMER_TAG" for target in node.targets)
        and isinstance(node.value, ast.Constant)
    ):
        print(node.value.value)
        break
PY
  )
  if [ -z "$source_anchor" ]; then
    die "Could not read CURRENT_CONSUMER_TAG from release_channels.py"
  fi
  if [ "$source_anchor" != "$tag" ]; then
    die "CURRENT_CONSUMER_TAG=${source_anchor} does not match latest release ${tag}"
  fi
  log "Verified source release anchor ${source_anchor}"
fi

installer_row=$(
  gh_release view "$tag" \
    --json assets \
    --jq '.assets[]
      | select(.name | test("^OysterRecorder-[Ss]etup-.*\\.exe$"))
      | [.name, .url, (.size | tostring), .digest]
      | @tsv' \
    | head -n 1
)

if [ -z "$installer_row" ]; then
  die "No OysterRecorder installer .exe asset found on ${tag}"
fi

IFS=$'\t' read -r installer_name installer_url installer_size installer_digest <<< "$installer_row"

if ! [[ "$installer_size" =~ ^[0-9]+$ ]] || [ "$installer_size" -le 0 ]; then
  die "Installer ${installer_name} has invalid size: ${installer_size}"
fi

if [[ "$installer_digest" != sha256:* ]]; then
  die "Installer ${installer_name} is missing a sha256 digest"
fi

sha_row=$(
  gh_release view "$tag" \
    --json assets \
    --jq '.assets[]
      | select(.name == "SHA-256-manifest.txt")
      | [.name, .url, (.size | tostring)]
      | @tsv' \
    | head -n 1
)

if [ -z "$sha_row" ]; then
  die "SHA-256-manifest.txt asset is missing on ${tag}"
fi

IFS=$'\t' read -r sha_name sha_url sha_size <<< "$sha_row"

if ! [[ "$sha_size" =~ ^[0-9]+$ ]] || [ "$sha_size" -le 0 ]; then
  die "${sha_name} has invalid size: ${sha_size}"
fi

log "Verifying release notes mention installer path"
body=$(gh_release view "$tag" --json body --jq '.body')
if ! grep -Fq "## Windows installer" <<< "$body"; then
  die "Release notes do not contain a Windows installer section"
fi

if ! grep -Fq "SmartScreen" <<< "$body"; then
  die "Release notes do not mention SmartScreen warning"
fi

log "Verifying public asset URLs"
curl -fsSIL -L "$installer_url" >/dev/null
curl -fsSIL -L "$sha_url" >/dev/null

sha_text=$(curl -fsSL "$sha_url")
expected_digest="${installer_digest#sha256:}"
if ! grep -Fq "${expected_digest}  ${installer_name}" <<< "$sha_text"; then
  die "SHA256SUMS.txt does not match ${installer_name}"
fi

log "PASS ${tag}: ${installer_name} (${installer_size} bytes)"
