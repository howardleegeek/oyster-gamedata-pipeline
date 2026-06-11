#!/usr/bin/env bash
# ============================================================================
# build_recorder_artifact.sh
# ============================================================================
# Local helper script to build the OysterRecorder Windows installer artifact
# on macOS or Linux. Uses cargo cross-compilation (via cargo-xwin or
# cargo-cross) when available, falling back to native build if on Windows.
#
# Usage:
#   ./scripts/build_recorder_artifact.sh              # auto-detect version
#   ./scripts/build_recorder_artifact.sh 1.2.3        # explicit version
#
# Requirements (macOS/Linux cross-build):
#   - Rust toolchain with x86_64-pc-windows-msvc target
#   - cargo-xwin  (cargo install cargo-xwin)  — preferred
#   OR
#   - cargo-cross (cargo install cross)
#   - Inno Setup 6.x via Wine (for .iss compilation)
#
# On native Windows:
#   - Just runs cargo build --release + iscc
# ============================================================================

set -euo pipefail

PROJECT_ROOT="${OYSTER_PROJECT_ROOT:-$(pwd)}"
RECORDER_DIR="$PROJECT_ROOT/vendor/recorder"
INSTALLER_SCRIPT="$PROJECT_ROOT/installer/oyster-recorder.iss"
OUTPUT_DIR="$PROJECT_ROOT/installer/output"

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# Resolve version
# ---------------------------------------------------------------------------
resolve_version() {
  local explicit_version="${1:-}"

  if [[ -n "$explicit_version" ]]; then
    echo "$explicit_version"
    return
  fi

  # Try Cargo.toml
  if [[ -f "$RECORDER_DIR/Cargo.toml" ]]; then
    local ver
    ver=$(grep -m1 '^version\s*=' "$RECORDER_DIR/Cargo.toml" | sed 's/.*"\(.*\)".*/\1/')
    if [[ -n "$ver" ]]; then
      echo "$ver"
      return
    fi
  fi

  # Try git tag
  local tag
  tag=$(git describe --tags --match 'recorder-v*' --abbrev=0 2>/dev/null || true)
  if [[ -n "$tag" ]]; then
    echo "${tag#recorder-v}"
    return
  fi

  echo "0.0.0-dev"
}

# ---------------------------------------------------------------------------
# Detect OS
# ---------------------------------------------------------------------------
detect_os() {
  case "$(uname -s)" in
    Linux*)  echo "linux" ;;
    Darwin*) echo "macos" ;;
    MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
    *)       echo "unknown" ;;
  esac
}

# ---------------------------------------------------------------------------
# Build Rust binary
# ---------------------------------------------------------------------------
build_rust() {
  local os="$1"

  cd "$RECORDER_DIR"

  case "$os" in
    windows)
      info "Native Windows build — cargo build --release"
      cargo build --release --locked
      ;;
    macos|linux)
      # Try cargo-xwin first (fastest, no VM needed)
      if command -v cargo-xwin &>/dev/null; then
        info "Cross-compiling with cargo-xwin (x86_64-pc-windows-msvc)"
        cargo xwin build --release --locked --target x86_64-pc-windows-msvc
      elif command -v cross &>/dev/null; then
        info "Cross-compiling with cargo-cross"
        cross build --release --locked --target x86_64-pc-windows-gnu
      else
        warn "Neither cargo-xwin nor cargo-cross found."
        warn "Attempting native build (will produce non-Windows binary)."
        warn "Install cargo-xwin:  cargo install cargo-xwin"
        cargo build --release --locked
      fi
      ;;
    *)
      error "Unsupported OS: $os"
      exit 1
      ;;
  esac

  # Verify binary exists
  local binary_name="oyster-recorder"
  if [[ "$os" == "windows" ]]; then
    binary_name="oyster-recorder.exe"
  fi

  if [[ -f "target/release/$binary_name" ]]; then
    info "✓ Built: target/release/$binary_name"
  else
    # Check for cross-compiled target
    local cross_target="x86_64-pc-windows-msvc"
    if [[ "$os" != "windows" ]] && command -v cross &>/dev/null && ! command -v cargo-xwin &>/dev/null; then
      cross_target="x86_64-pc-windows-gnu"
    fi
    if [[ -f "target/$cross_target/release/$binary_name" ]]; then
      info "✓ Built: target/$cross_target/release/$binary_name"
    else
      error "✗ Binary not found after build"
      exit 1
    fi
  fi

  cd "$PROJECT_ROOT"
}

# ---------------------------------------------------------------------------
# Compile Inno Setup installer
# ---------------------------------------------------------------------------
compile_installer() {
  local version="$1"
  local os="$2"

  mkdir -p "$OUTPUT_DIR"

  case "$os" in
    windows)
      info "Compiling installer with ISCC (native Windows)"
      iscc "/DAppVersion=$version" \
           "/DSourceDir=vendor\\recorder\\target\\release" \
           "$INSTALLER_SCRIPT"
      ;;
    macos|linux)
      if command -v iscc &>/dev/null; then
        info "Compiling installer with ISCC (native/wine)"
        iscc "/DAppVersion=$version" \
             "/DSourceDir=vendor/recorder/target/release" \
             "$INSTALLER_SCRIPT"
      elif command -v wine &>/dev/null; then
        # Try to find Inno Setup via Wine
        local iscc_wine
        iscc_wine=$(find ~/.wine -name "ISCC.exe" 2>/dev/null | head -1 || true)
        if [[ -n "$iscc_wine" ]]; then
          info "Compiling installer with Wine + Inno Setup"
          wine "$iscc_wine" "/DAppVersion=$version" \
               "/DSourceDir=vendor\\recorder\\target\\release" \
               "$INSTALLER_SCRIPT"
        else
          warn "Inno Setup not found. Skipping installer compilation."
          warn "Install Inno Setup 6.x or run this script on Windows."
          return 0
        fi
      else
        warn "Neither ISCC nor Wine found. Skipping installer compilation."
        warn "Install Inno Setup 6.x or run this script on Windows."
        return 0
      fi
      ;;
  esac

  # Verify output
  local setup_exe="$OUTPUT_DIR/OysterRecorder-setup-v${version}.exe"
  if [[ -f "$setup_exe" ]]; then
    info "✓ Installer: $setup_exe"
    ls -lh "$setup_exe"
  else
    # ISCC may output to a different location; search for it
    local found
    found=$(find "$PROJECT_ROOT" -name "OysterRecorder-setup-v${version}.exe" 2>/dev/null | head -1 || true)
    if [[ -n "$found" ]]; then
      info "✓ Installer found at: $found"
      mkdir -p "$OUTPUT_DIR"
      cp "$found" "$OUTPUT_DIR/"
    else
      warn "Installer exe not found in expected location."
      warn "Check ISCC output above for the actual output path."
    fi
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  local version="${1:-}"
  version=$(resolve_version "$version")
  local os
  os=$(detect_os)

  info "=========================================="
  info " OysterRecorder Build"
  info " Version : $version"
  info " Platform: $os"
  info "=========================================="

  # Check vendor/recorder exists
  if [[ ! -d "$RECORDER_DIR" ]]; then
    error "vendor/recorder not found. Run: git submodule update --init --recursive"
    exit 1
  fi

  # Check Rust
  if ! command -v cargo &>/dev/null; then
    error "Rust/Cargo not found. Install from https://rustup.rs"
    exit 1
  fi

  # Step 1: Build Rust binary
  build_rust "$os"

  # Step 2: Compile installer
  compile_installer "$version" "$os"

  info "=========================================="
  info " Build complete!"
  info "=========================================="
}

main "$@"
