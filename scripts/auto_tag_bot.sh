#!/usr/bin/env bash
# auto_tag_bot.sh — After N commits to main since last tag, auto-bump patch
# version, tag + push, and create a GitHub release.
#
# Usage:
#   bash scripts/auto_tag_bot.sh
#
# Environment:
#   GITHUB_TOKEN     — required for gh CLI
#   DRY_RUN          — set to "true" to skip actual tag/push/release
#   COMMIT_THRESHOLD — minimum commits since last tag to trigger (default: 3)
#   LATEST_TAG       — optional override for the last known tag
#
# SemVer rules:
#   Only bumps PATCH.  Major/minor are never auto-bumped.
#
# Release body:
#   Includes git log range + spec IDs parsed from commit messages (S\d+).

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
  echo "[auto-tag-bot] $*"
}

die() {
  echo "[auto-tag-bot] ERROR: $*" >&2
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

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

if [ "${DRY_RUN:-false}" != "true" ] && ! command -v gh &>/dev/null; then
  die "gh CLI is required but not found"
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
  die "GITHUB_TOKEN environment variable is not set"
fi

DRY_RUN="${DRY_RUN:-false}"
COMMIT_THRESHOLD="${COMMIT_THRESHOLD:-3}"

# ---------------------------------------------------------------------------
# Determine latest tag
# ---------------------------------------------------------------------------

LATEST_TAG="${LATEST_TAG:-}"

if [ -z "$LATEST_TAG" ]; then
  LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
fi

# ---------------------------------------------------------------------------
# Count commits since last tag
# ---------------------------------------------------------------------------

if [ -z "$LATEST_TAG" ]; then
  COMMIT_COUNT=$(git rev-list HEAD --count)
  TAG_RANGE="HEAD"
else
  COMMIT_COUNT=$(git rev-list "${LATEST_TAG}..HEAD" --count)
  TAG_RANGE="${LATEST_TAG}..HEAD"
fi

log "Commits since last tag: ${COMMIT_COUNT} (threshold: ${COMMIT_THRESHOLD})"

if [ "$COMMIT_COUNT" -lt "$COMMIT_THRESHOLD" ]; then
  log "Commit count ${COMMIT_COUNT} is below threshold ${COMMIT_THRESHOLD}; skipping."
  exit 0
fi

# ---------------------------------------------------------------------------
# Compute new version (patch bump only)
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

VERSION_NUM="${CURRENT_VERSION#v}"

if ! echo "$VERSION_NUM" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  die "Invalid SemVer in tag ${CURRENT_VERSION}: expected vMAJOR.MINOR.PATCH"
fi

MAJOR="${VERSION_NUM%%.*}"
REST="${VERSION_NUM#*.}"
MINOR="${REST%%.*}"
PATCH="${REST#*.}"

PATCH=$(( PATCH + 1 ))

NEW_VERSION="v${MAJOR}.${MINOR}.${PATCH}"
while tag_exists "$NEW_VERSION"; do
  log "Tag ${NEW_VERSION} already exists; bumping patch again"
  PATCH=$(( PATCH + 1 ))
  NEW_VERSION="v${MAJOR}.${MINOR}.${PATCH}"
done
log "New version: ${NEW_VERSION} (from ${CURRENT_VERSION})"

# ---------------------------------------------------------------------------
# Build release body: git log range + spec IDs
# ---------------------------------------------------------------------------

TODAY=$(date +%Y-%m-%d)

# Collect commit subjects in the range
LOG_OUTPUT=$(git log --format="%h %s" "${TAG_RANGE}")

# Parse spec IDs (S\d+) from commit messages
SPEC_IDS=$(git log --format="%s" "${TAG_RANGE}" | grep -oE 'S[0-9]+' | sort -u || true)

# Build the release body
RELEASE_BODY="## [${NEW_VERSION#v}] - ${TODAY}

### Commits

\`\`\`
${LOG_OUTPUT}
\`\`\`"

if [ -n "$SPEC_IDS" ]; then
  SPEC_LIST=""
  while IFS= read -r sid; do
    SPEC_LIST="${SPEC_LIST}
- ${sid}"
  done <<< "$SPEC_IDS"

  RELEASE_BODY="${RELEASE_BODY}

### Spec IDs

${SPEC_LIST}"
fi

log "Release body built"

# ---------------------------------------------------------------------------
# Dry-run or actual release
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" = "true" ]; then
  log "DRY_RUN=true — skipping tag, push, and release creation"
  log "Would create tag: ${NEW_VERSION}"
  log "Would create release with body:"
  echo "$RELEASE_BODY"
  exit 0
fi

# Create and push tag
git tag -a "$NEW_VERSION" -m "Release ${NEW_VERSION}"
git push origin "$NEW_VERSION"

# Create GitHub release
gh release create "$NEW_VERSION" \
  --title "Release ${NEW_VERSION}" \
  --notes "$RELEASE_BODY" \
  --generate-notes=false

log "Release ${NEW_VERSION} created successfully"
