#!/usr/bin/env bash
set -euo pipefail

# R014 · bin/release.sh — 自动化 GitHub release 发布

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Global variables
VERSION=""
PRERELEASE=false
STABLE=false
AUTO_BUMP=false
DRY_RUN=false
PREV_TAG=""
RELEASE_NOTES_FILE=""

# Print colored messages
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Print usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Automate GitHub release creation with version management, testing, and release notes.

Options:
  --version VERSION      Specify version tag (e.g., v0.1.0-rc3)
  --auto-bump            Auto-increment patch version from latest tag
  --prerelease           Mark release as prerelease
  --stable               Mark release as stable (default if not prerelease)
  --dry-run              Show what would be done without actually doing it
  -h, --help             Show this help message

Examples:
  $0 --version v0.1.0-rc3 --prerelease
  $0 --auto-bump
  $0 --version v0.2.0 --stable
EOF
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --version)
                VERSION="$2"
                shift 2
                ;;
            --auto-bump)
                AUTO_BUMP=true
                shift
                ;;
            --prerelease)
                PRERELEASE=true
                shift
                ;;
            --stable)
                STABLE=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
    
    # Validate argument combinations
    if [[ -n "$VERSION" && "$AUTO_BUMP" = true ]]; then
        error "Cannot specify both --version and --auto-bump"
        exit 1
    fi
    
    if [[ -z "$VERSION" && "$AUTO_BUMP" = false ]]; then
        error "Either --version or --auto-bump must be specified"
        exit 1
    fi
    
    # Default to stable if not prerelease
    if [[ "$PRERELEASE" = false && "$STABLE" = false ]]; then
        STABLE=true
    fi
}

# Step 1: Preflight checks
preflight() {
    info "Running preflight checks..."
    
    # Check if gh CLI is installed
    if ! command -v gh &> /dev/null; then
        error "GitHub CLI (gh) is not installed. Please install it first."
        exit 1
    fi
    
    # Check if authenticated with GitHub CLI
    if ! gh auth status &> /dev/null; then
        error "Not authenticated with GitHub CLI. Run 'gh auth login' first."
        exit 1
    fi
    
    # Check if git repository is clean
    if [[ -n "$(git status --porcelain)" ]]; then
        error "Git repository has uncommitted changes. Please commit or stash them first."
        exit 1
    fi
    
    # Check if on main branch
    CURRENT_BRANCH=$(git branch --show-current)
    if [[ "$CURRENT_BRANCH" != "main" ]]; then
        error "Not on main branch. Current branch: $CURRENT_BRANCH"
        exit 1
    fi
    
    success "Preflight checks passed"
}

# Step 2: Extract version number
extract_version() {
    info "Determining version..."
    
    if [[ "$AUTO_BUMP" = true ]]; then
        # Get the latest tag
        LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || true)
        
        if [[ -z "$LATEST_TAG" ]]; then
            VERSION="v0.1.0"
            info "No existing tags found, starting with $VERSION"
        else
            # Extract version parts
            if [[ "$LATEST_TAG" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)(-.*)?$ ]]; then
                MAJOR="${BASH_REMATCH[1]}"
                MINOR="${BASH_REMATCH[2]}"
                PATCH="${BASH_REMATCH[3]}"
                SUFFIX="${BASH_REMATCH[4]:-}"
                
                # Increment patch version
                NEW_PATCH=$((PATCH + 1))
                VERSION="v${MAJOR}.${MINOR}.${NEW_PATCH}${SUFFIX}"
                info "Auto-bumped version: $LATEST_TAG → $VERSION"
            else
                error "Failed to parse version from tag: $LATEST_TAG"
                exit 1
            fi
        fi
    fi
    
    # Validate version format
    if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9\.]+)?$ ]]; then
        error "Invalid version format: $VERSION. Expected format: vX.Y.Z or vX.Y.Z-suffix"
        exit 1
    fi
    
    success "Version determined: $VERSION"
}

# Step 3: Verify version not already used
verify_version() {
    info "Verifying version $VERSION is not already used..."
    
    if gh release view "$VERSION" &>/dev/null; then
        error "Release $VERSION already exists on GitHub"
        exit 1
    fi
    
    if git rev-parse "$VERSION" &>/dev/null; then
        error "Tag $VERSION already exists locally"
        exit 1
    fi
    
    success "Version $VERSION is available"
}

# Step 4: Run tests
run_tests() {
    info "Running tests..."
    
    if command -v pytest &> /dev/null; then
        if ! pytest; then
            error "Tests failed. Aborting release."
            exit 1
        fi
        success "Tests passed"
    else
        warning "pytest not found, skipping tests"
    fi
}

# Step 5: Run lint
run_lint() {
    info "Running lint checks..."
    
    # Check shell scripts
    if command -v shellcheck &> /dev/null; then
        info "Running shellcheck on bin/*.sh..."
        if ! shellcheck bin/*.sh; then
            error "shellcheck failed. Aborting release."
            exit 1
        fi
        success "shellcheck passed"
    else
        warning "shellcheck not found, skipping shell script linting"
    fi
    
    # Check Python code
    if command -v black &> /dev/null; then
        info "Running black --check..."
        if ! black --check .; then
            error "black check failed. Please format code with 'black .'"
            exit 1
        fi
        success "black check passed"
    else
        warning "black not found, skipping Python formatting check"
    fi
    
    success "All lint checks passed"
}

# Step 6: Generate release notes
generate_release_notes() {
    info "Generating release notes..."
    
    # Find previous tag
    PREV_TAG=$(git describe --tags --abbrev=0 2>/dev/null || true)
    
    RELEASE_NOTES_FILE="/tmp/release_notes_${VERSION}.md"
    
    cat > "$RELEASE_NOTES_FILE" << EOF
# Release $VERSION

EOF
    
    if [[ -n "$PREV_TAG" ]]; then
        cat >> "$RELEASE_NOTES_FILE" << EOF
## What's new (since $PREV_TAG)

EOF
        
        # Get commits since last tag
        git log --pretty=format:"- %s" "${PREV_TAG}..HEAD" >> "$RELEASE_NOTES_FILE" 2>/dev/null || true
        
        cat >> "$RELEASE_NOTES_FILE" << EOF

## Contributors

EOF
        
        # Get contributors since last tag
        git log --pretty=format:"- %an" "${PREV_TAG}..HEAD" | sort -u >> "$RELEASE_NOTES_FILE" 2>/dev/null || true
        
    else
        cat >> "$RELEASE_NOTES_FILE" << EOF
## Initial Release

EOF
        
        # Get all commits
        git log --pretty=format:"- %s" >> "$RELEASE_NOTES_FILE" 2>/dev/null || true
        
        cat >> "$RELEASE_NOTES_FILE" << EOF

## Contributors

EOF
        
        # Get all contributors
        git log --pretty=format:"- %an" | sort -u >> "$RELEASE_NOTES_FILE" 2>/dev/null || true
    fi
    
    # Add release type
    if [[ "$PRERELEASE" = true ]]; then
        echo -e "\n**This is a prerelease**" >> "$RELEASE_NOTES_FILE"
    fi
    
    info "Release notes generated at: $RELEASE_NOTES_FILE"
    echo "--- Release Notes Preview ---"
    cat "$RELEASE_NOTES_FILE"
    echo "--- End Preview ---"
}

# Step 7: Create git tag
create_git_tag() {
    info "Creating git tag: $VERSION"
    
    if [[ "$DRY_RUN" = true ]]; then
        info "[DRY RUN] Would run: git tag -a $VERSION -m \"Release $VERSION\""
        info "[DRY RUN] Would run: git push origin $VERSION"
        return
    fi
    
    git tag -a "$VERSION" -m "Release $VERSION"
    git push origin "$VERSION"
    
    success "Tag $VERSION created and pushed"
}

# Step 8: Create GitHub release
create_github_release() {
    info "Creating GitHub release: $VERSION"
    
    # Build gh release create arguments
    local GH_ARGS=()
    GH_ARGS+=("$VERSION")
    GH_ARGS+=("--title" "Release $VERSION")
    GH_ARGS+=("--notes-file" "$RELEASE_NOTES_FILE")
    
    if [[ "$PRERELEASE" = true ]]; then
        GH_ARGS+=("--prerelease")
    fi
    
    # Add assets if they exist
    if [[ -d "samples" ]]; then
        # Use nullglob to handle no matching files
        shopt -s nullglob
        for asset in samples/*.tar.gz samples/*.zip; do
            if [[ -f "$asset" ]]; then
                GH_ARGS+=("$asset")
                info "Adding asset: $asset"
            fi
        done
        shopt -u nullglob
    fi
    
    if [[ "$DRY_RUN" = true ]]; then
        info "[DRY RUN] Would run: gh release create ${GH_ARGS[*]}"
        return
    fi
    
    gh release create "${GH_ARGS[@]}"
    
    success "GitHub release $VERSION created"
}

# Cleanup function
cleanup() {
    if [[ -f "$RELEASE_NOTES_FILE" ]]; then
        rm -f "$RELEASE_NOTES_FILE"
    fi
}

# Main execution
main() {
    trap cleanup EXIT
    
    parse_args "$@"
    
    info "Starting release process for version: $VERSION"
    info "Mode: $([[ "$DRY_RUN" = true ]] && echo "DRY RUN" || echo "LIVE")"
    info "Type: $([[ "$PRERELEASE" = true ]] && echo "PRERELEASE" || echo "STABLE")"
    
    # Execute steps
    preflight
    extract_version
    verify_version
    run_tests
    run_lint
    generate_release_notes
    create_git_tag
    create_github_release
    
    success "Release process completed successfully!"
    info "Release URL: https://github.com/$(gh repo view --json nameWithOwner -q '.nameWithOwner')/releases/tag/$VERSION"
}

# Run main function
main "$@"