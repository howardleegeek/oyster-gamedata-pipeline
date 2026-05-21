#!/usr/bin/env bash
# auto_release.sh — Build CHANGELOG entry, bump SemVer, tag + push, create GitHub release.
#
# Usage: bash scripts/auto_release.sh
#
# Environment:
#   GITHUB_TOKEN  — required for gh CLI
#   DRY_RUN       — set to "true" to skip actual tag/push/release
#
# SemVer rules:
#   BREAKING CHANGE in any commit message → bump major
#   feat: prefix in any commit message    → bump minor
#   otherwise                             → bump patch

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
  echo "[auto-release] $*"
}

die() {
  echo "[auto-release] ERROR: $*" >&2
  exit 1
}

latest_semver_tag() {
  git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-version:refname \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
    | head -n 1 || true
}

tag_exists() {
  git rev-parse -q --verify "refs/tags/$1" >/dev/null
}

hash_files() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}

run_with_retries() {
  local attempts="${GITHUB_API_RETRIES:-3}"
  local delay="${GITHUB_API_RETRY_DELAY_SEC:-2}"
  local attempt=1

  while true; do
    if "$@"; then
      return 0
    fi

    if [ "$attempt" -ge "$attempts" ]; then
      return 1
    fi

    echo "[auto-release] WARN: command failed, retrying in ${delay}s: $*" >&2
    sleep "$delay"
    attempt=$(( attempt + 1 ))
    delay=$(( delay * 2 ))
  done
}

INSTALLER_ASSET_TMPDIR=""
INSTALLER_ASSET_FILES=()

cleanup_installer_asset_tmpdir() {
  if [ -n "${INSTALLER_ASSET_TMPDIR:-}" ]; then
    rm -rf "$INSTALLER_ASSET_TMPDIR"
    INSTALLER_ASSET_TMPDIR=""
  fi
}

trap cleanup_installer_asset_tmpdir EXIT

prepare_latest_installer_assets() {
  local target_tag="$1"

  cleanup_installer_asset_tmpdir
  INSTALLER_ASSET_FILES=()

  if [ "${ATTACH_INSTALLER_ASSETS:-true}" = "false" ]; then
    log "ATTACH_INSTALLER_ASSETS=false — skipping installer asset copy"
    return 0
  fi

  local tmpdir source_tag candidate
  tmpdir=$(mktemp -d)
  INSTALLER_ASSET_TMPDIR="$tmpdir"

  local release_tags
  release_tags=$(
    run_with_retries gh release list \
      --limit "${INSTALLER_ASSET_SEARCH_LIMIT:-30}" \
      --json tagName,isDraft,isPrerelease \
      --jq '.[] | select((.isDraft | not) and (.isPrerelease | not)) | .tagName'
  ) || {
    cleanup_installer_asset_tmpdir
    die "Could not list GitHub releases while looking for installer assets"
  }

  while IFS= read -r candidate; do
    if [ -z "$candidate" ]; then
      continue
    fi

    if [ "$candidate" = "$target_tag" ]; then
      continue
    fi

    rm -f "$tmpdir"/OysterRecorder-setup-*.exe "$tmpdir"/OysterRecorder-Setup-*.exe
    if run_with_retries gh release download "$candidate" \
      --pattern 'OysterRecorder-[Ss]etup-*.exe' \
      --dir "$tmpdir" >/dev/null 2>&1; then
      shopt -s nullglob
      local candidate_installers=(
        "$tmpdir"/OysterRecorder-setup-*.exe
        "$tmpdir"/OysterRecorder-Setup-*.exe
      )
      shopt -u nullglob

      if [ "${#candidate_installers[@]}" -gt 0 ]; then
        log "Found installer assets on ${candidate}"
        source_tag="$candidate"
        break
      fi
    fi
  done <<< "$release_tags"

  if [ -z "${source_tag:-}" ]; then
    cleanup_installer_asset_tmpdir
    die "No known-good OysterRecorder installer asset found to attach to ${target_tag}"
  fi

  log "Preparing installer assets from ${source_tag} for ${target_tag}"
  shopt -s nullglob
  local installers=("$tmpdir"/OysterRecorder-setup-*.exe "$tmpdir"/OysterRecorder-Setup-*.exe)
  shopt -u nullglob

  if [ "${#installers[@]}" -eq 0 ]; then
    cleanup_installer_asset_tmpdir
    die "Release ${source_tag} had no downloadable OysterRecorder installer after download"
  fi

  # Re-download from the chosen source in case the candidate loop left partial files.
  rm -f "$tmpdir"/OysterRecorder-setup-*.exe "$tmpdir"/OysterRecorder-Setup-*.exe
  if run_with_retries gh release download "$source_tag" \
    --pattern 'OysterRecorder-[Ss]etup-*.exe' \
    --dir "$tmpdir"; then
    shopt -s nullglob
    installers=("$tmpdir"/OysterRecorder-setup-*.exe "$tmpdir"/OysterRecorder-Setup-*.exe)
    shopt -u nullglob

    if [ "${#installers[@]}" -eq 0 ]; then
      cleanup_installer_asset_tmpdir
      die "Release ${source_tag} had no downloadable OysterRecorder installer after retry"
    fi
  else
    cleanup_installer_asset_tmpdir
    die "Could not download installer assets from ${source_tag}"
  fi

  local installer_names=()
  local installer
  for installer in "${installers[@]}"; do
    installer_names+=("$(basename "$installer")")
  done

  (
    cd "$tmpdir"
    hash_files "${installer_names[@]}" | sort > SHA256SUMS.txt
  )

  INSTALLER_ASSET_FILES=("${installers[@]}" "$tmpdir/SHA256SUMS.txt")
  log "Installer assets prepared for ${target_tag}"
}

attach_latest_installer_assets() {
  local target_tag="$1"

  if [ "${ATTACH_INSTALLER_ASSETS:-true}" = "false" ]; then
    log "ATTACH_INSTALLER_ASSETS=false — skipping installer asset upload"
    return 0
  fi

  if [ "${#INSTALLER_ASSET_FILES[@]}" -eq 0 ]; then
    prepare_latest_installer_assets "$target_tag"
  fi

  if [ "${#INSTALLER_ASSET_FILES[@]}" -eq 0 ]; then
    die "No prepared installer assets are available for ${target_tag}"
  fi

  if run_with_retries gh release upload \
    "$target_tag" "${INSTALLER_ASSET_FILES[@]}" --clobber; then
    log "Installer assets attached to ${target_tag}"
  else
    die "Could not upload installer assets to ${target_tag}"
  fi
}

installer_release_notes() {
  local target_tag="$1"

  if [ "${ATTACH_INSTALLER_ASSETS:-true}" = "false" ]; then
    return 0
  fi

  if [ "${#INSTALLER_ASSET_FILES[@]}" -eq 0 ]; then
    return 0
  fi

  local repo_slug="${RELEASE_REPOSITORY:-${GITHUB_REPOSITORY:-}}"
  local asset asset_name

  echo ""
  echo ""
  echo "## Windows installer"
  echo ""

  for asset in "${INSTALLER_ASSET_FILES[@]}"; do
    asset_name=$(basename "$asset")
    if [[ "$asset_name" == *.exe ]]; then
      if [ -n "$repo_slug" ]; then
        echo "- Download: [${asset_name}](https://github.com/${repo_slug}/releases/download/${target_tag}/${asset_name})"
      else
        echo "- Download: ${asset_name} (attached to this release)"
      fi
    fi
  done

  echo "- Checksum: \`SHA256SUMS.txt\`"
  echo "- Windows SmartScreen may warn until the installer is code-signed."
  echo "- Installs to \`%LOCALAPPDATA%\\OysterRecorder\\\` and starts from the tray."
  echo "- Backend income/upload endpoints may still be test-mode until public deploy is complete."
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

if ! command -v gh &>/dev/null; then
  die "gh CLI is required but not found"
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
  die "GITHUB_TOKEN environment variable is not set"
fi

DRY_RUN="${DRY_RUN:-false}"

# ---------------------------------------------------------------------------
# Determine latest tag
# ---------------------------------------------------------------------------

LATEST_TAG="${LATEST_TAG:-}"

if [ -z "$LATEST_TAG" ]; then
  LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
fi

# ---------------------------------------------------------------------------
# Collect commits since last tag
# ---------------------------------------------------------------------------

if [ -z "$LATEST_TAG" ]; then
  # No previous tag — collect all commits
  COMMITS=$(git log --format="%H %s" --reverse)
else
  COMMITS=$(git log --format="%H %s" "${LATEST_TAG}..HEAD" --reverse)
fi

if [ -z "$COMMITS" ]; then
  die "No commits found since last tag"
fi

# ---------------------------------------------------------------------------
# Determine bump type from commit messages
# ---------------------------------------------------------------------------

BUMP_TYPE="patch"

while IFS= read -r line; do
  subject="${line#* }"  # everything after the hash

  # Check for BREAKING CHANGE (Conventional Commits footer or body)
  if echo "$subject" | grep -qiE 'BREAKING[[:space:]]*CHANGE|!:'; then
    BUMP_TYPE="major"
    break
  fi
done <<< "$COMMITS"

# Only bump to minor if not already major
if [ "$BUMP_TYPE" != "major" ]; then
  while IFS= read -r line; do
    subject="${line#* }"
    if echo "$subject" | grep -qiE '^feat(\(.+\))?:'; then
      BUMP_TYPE="minor"
      break
    fi
  done <<< "$COMMITS"
fi

log "Bump type: ${BUMP_TYPE}"

# ---------------------------------------------------------------------------
# Parse current version and compute new version
# ---------------------------------------------------------------------------

VERSION_BASE_TAG="${VERSION_BASE_TAG:-}"

if [ -z "$VERSION_BASE_TAG" ]; then
  VERSION_BASE_TAG=$(latest_semver_tag)
fi

if [ -n "$VERSION_BASE_TAG" ]; then
  CURRENT_VERSION="$VERSION_BASE_TAG"
elif [ -n "$LATEST_TAG" ]; then
  CURRENT_VERSION="$LATEST_TAG"
else
  CURRENT_VERSION="v0.0.0"
fi

# Strip leading 'v'
VERSION_NUM="${CURRENT_VERSION#v}"

# Validate SemVer
if ! echo "$VERSION_NUM" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  die "Invalid SemVer in tag ${CURRENT_VERSION}: expected vMAJOR.MINOR.PATCH"
fi

MAJOR="${VERSION_NUM%%.*}"
REST="${VERSION_NUM#*.}"
MINOR="${REST%%.*}"
PATCH="${REST#*.}"

case "$BUMP_TYPE" in
  major)
    MAJOR=$(( MAJOR + 1 ))
    MINOR=0
    PATCH=0
    ;;
  minor)
    MINOR=$(( MINOR + 1 ))
    PATCH=0
    ;;
  patch)
    PATCH=$(( PATCH + 1 ))
    ;;
esac

NEW_VERSION="v${MAJOR}.${MINOR}.${PATCH}"
while tag_exists "$NEW_VERSION"; do
  log "Tag ${NEW_VERSION} already exists; bumping patch again"
  PATCH=$(( PATCH + 1 ))
  NEW_VERSION="v${MAJOR}.${MINOR}.${PATCH}"
done
log "New version: ${NEW_VERSION} (from ${CURRENT_VERSION})"

# ---------------------------------------------------------------------------
# Build CHANGELOG segment (Keep-a-Changelog format)
# ---------------------------------------------------------------------------

TODAY=$(date +%Y-%m-%d)

CHANGELOG_HEADER="## [${NEW_VERSION#v}] - ${TODAY}"

# Categorise commits
FEAT_ENTRIES=""
FIX_ENTRIES=""
OTHER_ENTRIES=""

while IFS= read -r line; do
  hash="${line%% *}"
  subject="${line#* }"

  # Short hash for display
  SHORT_HASH=$(echo "$hash" | cut -c1-7)

  if echo "$subject" | grep -qiE '^feat(\(.+\))?:'; then
    CLEAN=$(echo "$subject" | sed -E 's/^feat(\(.+\))?:[[:space:]]*//')
    FEAT_ENTRIES="${FEAT_ENTRIES}
- ${CLEAN} (${SHORT_HASH})"
  elif echo "$subject" | grep -qiE '^fix(\(.+\))?:'; then
    CLEAN=$(echo "$subject" | sed -E 's/^fix(\(.+\))?:[[:space:]]*//')
    FIX_ENTRIES="${FIX_ENTRIES}
- ${CLEAN} (${SHORT_HASH})"
  else
    OTHER_ENTRIES="${OTHER_ENTRIES}
- ${subject} (${SHORT_HASH})"
  fi
done <<< "$COMMITS"

CHANGELOG_BODY="${CHANGELOG_HEADER}"

if [ -n "$FEAT_ENTRIES" ]; then
  CHANGELOG_BODY="${CHANGELOG_BODY}

### Added
${FEAT_ENTRIES}"
fi

if [ -n "$FIX_ENTRIES" ]; then
  CHANGELOG_BODY="${CHANGELOG_BODY}

### Fixed
${FIX_ENTRIES}"
fi

if [ -n "$OTHER_ENTRIES" ]; then
  CHANGELOG_BODY="${CHANGELOG_BODY}

### Other
${OTHER_ENTRIES}"
fi

log "CHANGELOG segment built"

if [ "$DRY_RUN" = "true" ]; then
  log "DRY_RUN=true — skipping tag, push, release creation, and CHANGELOG write"
  log "Would create tag: ${NEW_VERSION}"
  log "Would create release with body:"
  echo "$CHANGELOG_BODY"
  exit 0
fi

# Pre-fetch assets before changing git state. For new releases, passing files
# directly to `gh release create` lets the CLI upload to a draft before publish.
prepare_latest_installer_assets "$NEW_VERSION"
RELEASE_BODY="${CHANGELOG_BODY}$(installer_release_notes "$NEW_VERSION")"

# ---------------------------------------------------------------------------
# Update CHANGELOG.md
# ---------------------------------------------------------------------------

if [ -f CHANGELOG.md ]; then
  FIRST_LINE=$(head -n1 CHANGELOG.md)
  if echo "$FIRST_LINE" | grep -qiE '^# '; then
    # Has a title line — insert after it
    {
      echo "$FIRST_LINE"
      echo ""
      echo "$CHANGELOG_BODY"
      echo ""
      tail -n +2 CHANGELOG.md
    } > CHANGELOG.md.tmp
    mv CHANGELOG.md.tmp CHANGELOG.md
  else
    {
      echo "$CHANGELOG_BODY"
      echo ""
      cat CHANGELOG.md
    } > CHANGELOG.md.tmp
    mv CHANGELOG.md.tmp CHANGELOG.md
  fi
else
  {
    echo "# Changelog"
    echo ""
    echo "All notable changes to this project will be documented in this file."
    echo ""
    echo "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),"
    echo "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)."
    echo ""
    echo "$CHANGELOG_BODY"
  } > CHANGELOG.md
fi

log "CHANGELOG.md updated"

# ---------------------------------------------------------------------------
# Commit, tag, push, release
# ---------------------------------------------------------------------------

# Commit CHANGELOG
git add CHANGELOG.md
git commit -m "chore(release): ${NEW_VERSION} [skip ci]" || true

# Push the CHANGELOG commit first so the release tag remains reachable from
# main. If main advanced meanwhile, this non-force push fails and the workflow
# retries on the next push rather than creating another orphaned release tag.
git push origin HEAD:main

# Create and push tag
git tag -a "$NEW_VERSION" -m "Release ${NEW_VERSION}"
git push origin "$NEW_VERSION"

# Create GitHub release
if gh release view "$NEW_VERSION" >/dev/null 2>&1; then
  run_with_retries gh release edit "$NEW_VERSION" \
    --title "Release ${NEW_VERSION}" \
    --notes "$RELEASE_BODY"
  attach_latest_installer_assets "$NEW_VERSION"
else
  run_with_retries gh release create "$NEW_VERSION" \
    "${INSTALLER_ASSET_FILES[@]}" \
    --title "Release ${NEW_VERSION}" \
    --notes "$RELEASE_BODY" \
    --generate-notes=false
fi

log "Release ${NEW_VERSION} created successfully"
