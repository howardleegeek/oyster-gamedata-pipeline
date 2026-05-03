#!/usr/bin/env bash
#
# GameData Onboarding Doctor · v1.0
# Checks vendor machine dependencies for PRD onboarding
#
set -euo pipefail

# Version
VERSION="1.0"

# Status codes
PASS=0
WARN=1
FAIL=2

# Global state
SKIP_NETWORK=false
JSON_OUTPUT=false
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

# Colors (with terminal guard)
if [ -t 1 ]; then
    RED=$(tput setaf 1 2>/dev/null || printf '\033[0;31m')
    GREEN=$(tput setaf 2 2>/dev/null || printf '\033[0;32m')
    YELLOW=$(tput setaf 3 2>/dev/null || printf '\033[0;33m')
    BOLD=$(tput bold 2>/dev/null || printf '\033[1m')
    RESET=$(tput sgr0 2>/dev/null || printf '\033[0m')
else
    RED=''
    GREEN=''
    YELLOW=''
    BOLD=''
    RESET=''
fi

# Platform detection
detect_platform() {
    local os
    os=$(uname -s)
    case "$os" in
        Darwin) echo "macos" ;;
        Linux)
            if [ -f /etc/os-release ]; then
                # shellcheck source=/dev/null
                . /etc/os-release 2>/dev/null || true
                echo "linux:${ID:-unknown}"
            else
                echo "linux"
            fi
            ;;
        CYGWIN*|MINGW*|MSYS*) echo "windows" ;;
        *) echo "unknown" ;;
    esac
}

# Check if running in WSL
is_wsl() {
    if [ -f /proc/version ]; then
        grep -qi microsoft /proc/version 2>/dev/null
    else
        return 1
    fi
}

# Get package manager
get_package_manager() {
    local platform
    platform=$(detect_platform)
    case "$platform" in
        macos) echo "brew" ;;
        linux:ubuntu|linux:debian|linux:pop) echo "apt" ;;
        linux:centos|linux:rhel|linux:fedora|linux:rocky|linux:almalinux) echo "yum" ;;
        *) echo "unknown" ;;
    esac
}

# Get install command for a package
get_install_cmd() {
    local package="$1"
    local pm
    pm=$(get_package_manager)
    case "$pm" in
        brew) echo "brew install $package" ;;
        apt) echo "sudo apt-get install -y $package" ;;
        yum) echo "sudo yum install -y $package" ;;
        *) echo "Install $package using your package manager" ;;
    esac
}

# Print header
print_header() {
    if [ "$JSON_OUTPUT" = false ]; then
        echo "═══════════════════════════════════════════════════════════════"
        echo "GameData Onboarding Doctor · v$VERSION"
        echo "═══════════════════════════════════════════════════════════════"
        echo
    fi
}

# Print footer
print_footer() {
    if [ "$JSON_OUTPUT" = false ]; then
        echo
        echo "═══════════════════════════════════════════════════════════════"
        echo "Summary: $PASS_COUNT PASS · $WARN_COUNT WARN · $FAIL_COUNT FAIL"
        echo "═══════════════════════════════════════════════════════════════"
        echo
        echo "Run again after fixing failures: bash bin/doctor.sh"
        echo
        echo "For full onboarding guide: docs/VENDOR_ONBOARDING.md"
    fi
}

# Print check result
print_check() {
    local num="$1"
    local name="$2"
    local status="$3"
    local message="$4"
    local hint="${5:-}"
    
    if [ "$JSON_OUTPUT" = true ]; then
        return
    fi
    
    local status_icon
    case "$status" in
        "$PASS")
            status_icon="✅"
            PASS_COUNT=$((PASS_COUNT + 1))
            ;;
        "$WARN")
            status_icon="⚠️ "
            WARN_COUNT=$((WARN_COUNT + 1))
            ;;
        "$FAIL")
            status_icon="❌"
            FAIL_COUNT=$((FAIL_COUNT + 1))
            ;;
    esac
    
    printf "[%d/10] %-25s %s %s\n" "$num" "$name" "$status_icon" "$message"
    if [ -n "$hint" ]; then
        printf "                                       %s\n" "$hint"
    fi
}

# Check 1: Operating System
check_os() {
    local os
    os=$(uname -s)
    
    case "$os" in
        Darwin)
            local version name
            version=$(sw_vers -productVersion 2>/dev/null || echo "unknown")
            name=$(sw_vers -productName 2>/dev/null || echo "macOS")
            print_check 1 "Operating System" "$PASS" "$name $version"
            return "$PASS"
            ;;
        Linux)
            if is_wsl; then
                if [ -f /etc/os-release ]; then
                    # shellcheck source=/dev/null
                    . /etc/os-release 2>/dev/null || true
                    print_check 1 "Operating System" "$PASS" "Linux (${NAME:-unknown}) via WSL"
                else
                    print_check 1 "Operating System" "$PASS" "Linux via WSL"
                fi
                return "$PASS"
            fi
            if [ -f /etc/os-release ]; then
                # shellcheck source=/dev/null
                . /etc/os-release 2>/dev/null || true
                print_check 1 "Operating System" "$PASS" "${PRETTY_NAME:-Linux}"
            else
                print_check 1 "Operating System" "$PASS" "Linux"
            fi
            return "$PASS"
            ;;
        CYGWIN*|MINGW*|MSYS*)
            print_check 1 "Operating System" "$FAIL" "Windows detected" "Please use WSL2"
            return "$FAIL"
            ;;
        *)
            print_check 1 "Operating System" "$WARN" "Unknown OS: $os"
            return "$WARN"
            ;;
    esac
}

# Check 2: CPU cores
check_cpu() {
    local cores
    cores=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "0")
    
    if [ "$cores" = "0" ]; then
        print_check 2 "CPU cores" "$WARN" "Could not determine"
        return "$WARN"
    fi
    
    if [ "$cores" -ge 4 ]; then
        print_check 2 "CPU cores" "$PASS" "$cores cores"
        return "$PASS"
    else
        print_check 2 "CPU cores" "$WARN" "$cores cores (recommend 4+)"
        return "$WARN"
    fi
}

# Check 3: RAM
check_ram() {
    local total_gb
    if [ "$(uname -s)" = "Darwin" ]; then
        total_gb=$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0f", $1/1024/1024/1024}' || echo "0")
    else
        total_gb=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo "0")
    fi
    
    if [ "$total_gb" = "0" ] || [ -z "$total_gb" ]; then
        print_check 3 "RAM" "$WARN" "Could not determine"
        return "$WARN"
    fi
    
    if [ "$total_gb" -ge 16 ]; then
        print_check 3 "RAM" "$PASS" "$total_gb GB"
        return "$PASS"
    else
        print_check 3 "RAM" "$WARN" "$total_gb GB (recommend 16+ GB)"
        return "$WARN"
    fi
}

# Check 4: Disk free
check_disk() {
    local free_gb avail_str
    
    # Try df -h and parse the Available column
    avail_str=$(df -h . 2>/dev/null | awk 'NR==2 {print $4}')
    
    if [ -n "$avail_str" ]; then
        # Parse size with suffix (G, Gi, T, Ti, M, Mi, etc.)
        # Remove 'i' suffix if present (e.g., Gi -> G)
        avail_str=$(echo "$avail_str" | sed 's/i$//')
        
        case "$avail_str" in
            *G)
                free_gb=$(echo "$avail_str" | sed 's/G$//' | awk '{printf "%.0f", $1}')
                ;;
            *T)
                free_gb=$(echo "$avail_str" | sed 's/T$//' | awk '{printf "%.0f", $1 * 1024}')
                ;;
            *M)
                free_gb=$(echo "$avail_str" | sed 's/M$//' | awk '{printf "%.0f", $1 / 1024}')
                ;;
            *K)
                free_gb=$(echo "$avail_str" | sed 's/K$//' | awk '{printf "%.0f", $1 / 1024 / 1024}')
                ;;
            *)
                # Assume bytes or no suffix - treat as small
                free_gb=0
                ;;
        esac
    fi
    
    if [ -z "$free_gb" ] || [ "$free_gb" = "" ]; then
        print_check 4 "Disk free" "$WARN" "Could not determine"
        return "$WARN"
    fi
    
    if [ "$free_gb" -ge 100 ]; then
        print_check 4 "Disk free" "$PASS" "$free_gb GB free"
        return "$PASS"
    else
        print_check 4 "Disk free" "$WARN" "$free_gb GB free (recommend 100+ GB)"
        return "$WARN"
    fi
}

# Check 5: Java 21
check_java() {
    local version major
    if ! command -v java &>/dev/null; then
        print_check 5 "Java 21" "$FAIL" "Not found" "Install: $(get_install_cmd openjdk@21)"
        return "$FAIL"
    fi
    
    # Parse Java version - handle both new (17, 21) and old (1.8) format
    version=$(java -version 2>&1 | head -1)
    
    # Try to extract version number
    if echo "$version" | grep -qE 'version "1\.[0-9]'; then
        # Old format: 1.8.x
        major=$(echo "$version" | grep -oE '1\.[0-9]+' | cut -d. -f2)
    else
        # New format: 17.x, 21.x
        major=$(echo "$version" | grep -oE 'version "[0-9]+' | grep -oE '[0-9]+')
    fi
    
    if [ -z "$major" ]; then
        print_check 5 "Java 21" "$WARN" "Could not parse version"
        return "$WARN"
    fi
    
    if [ "$major" = "21" ]; then
        print_check 5 "Java 21" "$PASS" "Java $major"
        return "$PASS"
    elif [ "$major" -gt 21 ]; then
        print_check 5 "Java 21" "$PASS" "Java $major"
        return "$PASS"
    else
        print_check 5 "Java 21" "$WARN" "Java $major (recommend 21)" "Update: $(get_install_cmd openjdk@21)"
        return "$WARN"
    fi
}

# Check 6: Python 3.11+
check_python() {
    local version major minor
    if ! command -v python3 &>/dev/null; then
        print_check 6 "Python 3.11+" "$FAIL" "Not found" "Install: $(get_install_cmd python@3.11)"
        return "$FAIL"
    fi
    
    version=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    
    if [ -z "$major" ] || [ -z "$minor" ]; then
        print_check 6 "Python 3.11+" "$WARN" "Could not parse version"
        return "$WARN"
    fi
    
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then
        print_check 6 "Python 3.11+" "$PASS" "Python $version"
        return "$PASS"
    elif [ "$major" -eq 3 ] && [ "$minor" -ge 9 ]; then
        print_check 6 "Python 3.11+" "$WARN" "Python $version (recommend 3.11+)" "Update: $(get_install_cmd python@3.11)"
        return "$WARN"
    else
        print_check 6 "Python 3.11+" "$FAIL" "Python $version (need 3.11+)" "Install: $(get_install_cmd python@3.11)"
        return "$FAIL"
    fi
}

# Check 7: ffmpeg 4.4+
check_ffmpeg() {
    local version major minor
    if ! command -v ffmpeg &>/dev/null; then
        print_check 7 "ffmpeg" "$FAIL" "Not found" "Install: $(get_install_cmd ffmpeg)"
        return "$FAIL"
    fi
    
    version=$(ffmpeg -version 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    
    if [ -z "$major" ] || [ -z "$minor" ]; then
        print_check 7 "ffmpeg" "$WARN" "Could not parse version"
        return "$WARN"
    fi
    
    if [ "$major" -gt 4 ] || { [ "$major" -eq 4 ] && [ "$minor" -ge 4 ]; }; then
        print_check 7 "ffmpeg" "$PASS" "ffmpeg $version"
        return "$PASS"
    else
        print_check 7 "ffmpeg" "$WARN" "ffmpeg $version (recommend 4.4+)" "Update: brew upgrade ffmpeg or $(get_install_cmd ffmpeg)"
        return "$WARN"
    fi
}

# Check 8: OpenEXR system lib
check_openexr() {
    local platform pm version
    platform=$(detect_platform)
    pm=$(get_package_manager)
    
    case "$platform" in
        macos)
            if command -v brew &>/dev/null && brew list openexr &>/dev/null 2>&1; then
                version=$(brew list --versions openexr 2>/dev/null | awk '{print $2}')
                print_check 8 "OpenEXR system lib" "$PASS" "$version"
                return "$PASS"
            else
                print_check 8 "OpenEXR system lib" "$FAIL" "Not found" "Install: brew install openexr"
                return "$FAIL"
            fi
            ;;
        linux:*)
            # Check for libOpenEXR in common locations
            if ldconfig -p 2>/dev/null | grep -q libOpenEXR; then
                print_check 8 "OpenEXR system lib" "$PASS" "Installed"
                return "$PASS"
            elif [ -f /usr/lib/x86_64-linux-gnu/libOpenEXR.so ] 2>/dev/null; then
                print_check 8 "OpenEXR system lib" "$PASS" "Installed"
                return "$PASS"
            elif [ -f /usr/local/lib/libOpenEXR.so ] 2>/dev/null; then
                print_check 8 "OpenEXR system lib" "$PASS" "Installed"
                return "$PASS"
            else
                print_check 8 "OpenEXR system lib" "$FAIL" "Not found" "Install: apt-get install libopenexr-dev"
                return "$FAIL"
            fi
            ;;
        *)
            print_check 8 "OpenEXR system lib" "$WARN" "Could not verify on this platform"
            return "$WARN"
            ;;
    esac
}

# Check 9: Network upload speed
check_network() {
    local speed_bps speed_mbps
    
    if [ "$SKIP_NETWORK" = true ]; then
        print_check 9 "Network upload speed" "$PASS" "Skipped (--no-network)"
        return "$PASS"
    fi
    
    if ! command -v curl &>/dev/null; then
        print_check 9 "Network upload speed" "$WARN" "curl not found, skipping"
        return "$WARN"
    fi
    
    # Test download speed as proxy for network capability
    speed_bps=$(curl -s -o /dev/null -w '%{speed_download}' \
        --connect-timeout 5 --max-time 10 \
        "https://speed.cloudflare.com/__down?bytes=5000000" 2>/dev/null || echo "0")
    
    if [ "$speed_bps" = "0" ] || [ -z "$speed_bps" ]; then
        print_check 9 "Network upload speed" "$WARN" "Could not measure (network error)"
        return "$WARN"
    fi
    
    # Convert to Mbps
    speed_mbps=$(echo "$speed_bps" | awk '{printf "%.0f", $1 * 8 / 1000000}')
    
    if [ "$speed_mbps" -ge 50 ]; then
        print_check 9 "Network upload speed" "$PASS" "~$speed_mbps Mbps"
        return "$PASS"
    else
        print_check 9 "Network upload speed" "$WARN" "~$speed_mbps Mbps (recommend 50+)"
        return "$WARN"
    fi
}

# Check 10: Optional Python packages
check_optional() {
    local missing=0
    local packages=("torch" "transformers" "OpenEXR")
    local pkg
    
    if [ "$JSON_OUTPUT" = false ]; then
        printf "[10/10] %-25s " "Optional Python packages"
    fi
    
    for pkg in "${packages[@]}"; do
        if ! python3 -c "import $pkg" 2>/dev/null; then
            missing=$((missing + 1))
        fi
    done
    
    if [ "$missing" -eq 0 ]; then
        if [ "$JSON_OUTPUT" = false ]; then
            echo "✅ All installed"
        fi
        PASS_COUNT=$((PASS_COUNT + 1))
        return "$PASS"
    else
        if [ "$JSON_OUTPUT" = false ]; then
            echo "⚠️  ($missing missing)"
            for pkg in "${packages[@]}"; do
                if ! python3 -c "import $pkg" 2>/dev/null; then
                    printf "   - %-25s ❌ pip install %s\n" "$pkg" "$pkg"
                fi
            done
        fi
        WARN_COUNT=$((WARN_COUNT + 1))
        return "$WARN"
    fi
}

# JSON output
print_json() {
    cat <<EOF
{
  "version": "$VERSION",
  "platform": "$(detect_platform)",
  "summary": {
    "pass": $PASS_COUNT,
    "warn": $WARN_COUNT,
    "fail": $FAIL_COUNT
  }
}
EOF
}

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --no-network)
            SKIP_NETWORK=true
            shift
            ;;
        --json)
            JSON_OUTPUT=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--no-network] [--json]"
            echo "  --no-network  Skip network speed test"
            echo "  --json        Output in JSON format"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Main
main() {
    print_header
    
    check_os
    check_cpu
    check_ram
    check_disk
    check_java
    check_python
    check_ffmpeg
    check_openexr
    check_network
    check_optional
    
    if [ "$JSON_OUTPUT" = true ]; then
        print_json
    else
        print_footer
    fi
    
    if [ "$FAIL_COUNT" -gt 0 ] || [ "$WARN_COUNT" -gt 0 ]; then
        exit 1
    fi
    exit 0
}

main