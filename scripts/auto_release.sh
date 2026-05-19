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

if [ -z "$LATEST_TAG" ]; then
  CURRENT_VERSION="v0.0.0"
else
  CURRENT_VERSION="$LATEST_TAG"
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

if [ "$DRY_RUN" = "true" ]; then
  log "DRY_RUN=true — skipping tag, push, and release creation"
  log "Would create tag: ${NEW_VERSION}"
  log "Would create release with body:"
  echo "$CHANGELOG_BODY"
  exit 0
fi

# Create and push tag
git tag -a "$NEW_VERSION" -m "Release ${NEW_VERSION}"
git push origin "$NEW_VERSION"

# Create GitHub release
gh release create "$NEW_VERSION" \
  --title "Release ${NEW_VERSION}" \
  --notes "$CHANGELOG_BODY" \
  --generate-notes=false

log "Release ${NEW_VERSION} created successfully"
