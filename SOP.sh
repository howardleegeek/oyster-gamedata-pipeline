#!/bin/bash
# SOP.sh — Standard Operating Procedure for new team members
# Run once on a fresh git clone to produce a validated buyer-spec tarball

set -euo pipefail

# Resolve repo root (script lives in scripts/ subdir)
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

echo "=========================================="
echo "SOP: Fresh Clone → Validated Buyer Spec"
echo "=========================================="

# -----------------------------------------------------------------------------
# STEP 1/8: Check prerequisites
# -----------------------------------------------------------------------------
echo "[STEP 1/8] Checking prerequisites..."

check_cmd() {
    if command -v "$1" &>/dev/null; then
        echo "  ✓ $1 found: $(command -v $1)"
        return 0
    else
        echo "  ✗ $1 NOT FOUND — install hint: $2"
        return 1
    fi
}

MISSING=0
# Prefer Homebrew openjdk@21 over Apple system java stub
JAVA_BIN="$(command -v java || true)"
for HB_JAVA in /opt/homebrew/opt/openjdk@21/bin/java /usr/local/opt/openjdk@21/bin/java; do
    [[ -x "$HB_JAVA" ]] && JAVA_BIN="$HB_JAVA" && break
done
if [[ -n "${JAVA_BIN:-}" && -x "$JAVA_BIN" ]]; then
    echo "  ✓ java found: $JAVA_BIN"
    export PATH="$(dirname "$JAVA_BIN"):$PATH"
else
    echo "  ✗ java NOT FOUND — install hint: brew install openjdk@21"
    MISSING=1
fi
check_cmd "node" "brew install node@18" || MISSING=1
check_cmd "ffmpeg" "brew install ffmpeg" || MISSING=1
check_cmd "python3" "brew install python3" || MISSING=1

if [[ "$MISSING" -eq 1 ]]; then
    echo "[ERROR] Missing prerequisites. Install above and re-run."
    exit 1
fi

# Verify Java 21+
JAVA_VER=$(java -version 2>&1 | head -1 | cut -d'"' -f2 | cut -d'.' -f1)
if [[ "$JAVA_VER" -lt 21 ]]; then
    echo "[ERROR] Java 21+ required, found Java $JAVA_VER"
    exit 1
fi

# Verify Node 18+
NODE_VER=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
if [[ "$NODE_VER" -lt 18 ]]; then
    echo "[ERROR] Node 18+ required, found v$NODE_VER"
    exit 1
fi

echo "[STEP 1/8] ✓ All prerequisites satisfied"

# -----------------------------------------------------------------------------
# STEP 2/8: Create .venv and install repo with pip
# -----------------------------------------------------------------------------
echo "[STEP 2/8] Setting up Python virtual environment..."

if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
    echo "  Created .venv"
fi

source .venv/bin/activate
pip install --upgrade pip >/dev/null 2>&1
pip install -e .[test] >/dev/null 2>&1
echo "[STEP 2/8] ✓ Installed repo with test dependencies"

# -----------------------------------------------------------------------------
# STEP 3/8: Install mineflayer npm dependencies
# -----------------------------------------------------------------------------
echo "[STEP 3/8] Installing mineflayer npm dependencies..."

if [[ -d "mineflayer" ]]; then
    (cd mineflayer && npm install)
    echo "[STEP 3/8] ✓ Installed mineflayer npm packages"
else
    echo "[ERROR] mineflayer directory not found"
    exit 1
fi

# -----------------------------------------------------------------------------
# STEP 4/8: Download Paper 1.20.4 server jar
# -----------------------------------------------------------------------------
echo "[STEP 4/8] Downloading Paper 1.20.4 server..."

CACHE_DIR="$REPO_ROOT/bin/.cache"
mkdir -p "$CACHE_DIR"

PAPER_JAR="$CACHE_DIR/paper-1.20.4-497.jar"
PAPER_URL="https://api.papermc.io/v2/projects/paper/versions/1.20.4/builds/497/downloads/paper-1.20.4-497.jar"

if [[ ! -f "$PAPER_JAR" ]]; then
    echo "  Downloading Paper server jar..."
    curl -fsSL -o "$PAPER_JAR" "$PAPER_URL"
    echo "  Downloaded to $PAPER_JAR"
else
    echo "  Using cached $PAPER_JAR"
fi

echo "[STEP 4/8] ✓ Paper server jar ready"

# -----------------------------------------------------------------------------
# STEP 5/8: Boot Paper server (configure eula.txt, server.properties)
# -----------------------------------------------------------------------------
echo "[STEP 5/8] Configuring and booting Paper server..."

SERVER_DIR="$REPO_ROOT/bin/server"
mkdir -p "$SERVER_DIR"
cd "$SERVER_DIR"

# Copy jar if not already there
if [[ ! -f "paper.jar" ]]; then
    cp "$PAPER_JAR" paper.jar
fi

# Accept EULA
if [[ ! -f "eula.txt" ]]; then
    echo "eula=true" > eula.txt
fi

# Write minimal server.properties
if [[ ! -f "server.properties" ]]; then
    cat > server.properties <<EOF
server-port=25565
online-mode=false
motd=SOP Test Server
max-players=1
EOF
fi

# Start Paper server in background
nohup java -Xmx512M -jar paper.jar nogui > server.log 2>&1 &
SERVER_PID=$!
echo "  Started Paper server (PID: $SERVER_PID)"

cd "$REPO_ROOT"
echo "[STEP 5/8] ✓ Paper server booting"

# -----------------------------------------------------------------------------
# STEP 6/8: Wait for Paper to listen on port 25565
# -----------------------------------------------------------------------------
echo "[STEP 6/8] Waiting for Paper server to start (port 25565)..."

MAX_WAIT=60
ELAPSED=0
while [[ $ELAPSED -lt $MAX_WAIT ]]; do
    if lsof -i :25565 -sTCP:LISTEN -t &>/dev/null; then
        echo "  ✓ Paper server is listening on port 25565"
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    echo "[ERROR] Paper server did not start within $MAX_WAIT seconds"
    cat "$SERVER_DIR/server.log"
    exit 1
fi

echo "[STEP 6/8] ✓ Paper server ready"

# -----------------------------------------------------------------------------
# STEP 7/8: Stage placeholder farm (1801 EXR hardlinks)
# -----------------------------------------------------------------------------
echo "[STEP 7/8] Staging placeholder farm..."

PLACEHOLDER_DIR="$REPO_ROOT/placeholders/depth"
mkdir -p "$PLACEHOLDER_DIR"

# Create a seed EXR file if it doesn't exist
SEED_FILE="$PLACEHOLDER_DIR/seed.exr"
if [[ ! -f "$SEED_FILE" ]]; then
    # Create a minimal EXR file (placeholder)
    # Using python to create a minimal EXR-like file for testing
    python3 << 'PYEOF'
import struct
import os
# Minimal EXR header (very simplified for placeholder)
# Real EXR files are complex; this creates a stub for hardlinking
with open("placeholders/depth/seed.exr", "wb") as f:
    # Write EXR magic number
    f.write(b'\x76\x2f\x31\x01')  # EXR file magic
    # Write a minimal header
    f.write(b'\x00')  # null terminator for empty channel list
PYEOF
fi

# Create 1801 hardlinks from seed
COUNT=1801
for i in $(seq 1 $COUNT); do
    LINK_PATH="$PLACEHOLDER_DIR/depth_$(printf "%04d" $i).exr"
    if [[ ! -f "$LINK_PATH" ]]; then
        ln "$SEED_FILE" "$LINK_PATH"
    fi
done

echo "  ✓ Created $COUNT hardlinks in $PLACEHOLDER_DIR"
echo "[STEP 7/8] ✓ Placeholder farm staged"

# -----------------------------------------------------------------------------
# STEP 8/8: Run end-to-end pipeline
# -----------------------------------------------------------------------------
echo "[STEP 8/8] Running end-to-end pipeline..."

# Run oyster-agent run-mc
echo "  → Running oyster-agent run-mc..."
if ! oyster-agent run-mc; then
    echo "[ERROR] oyster-agent run-mc failed"
    exit 1
fi

# Run adapt-buyer-spec
echo "  → Running adapt-buyer-spec..."
if ! adapt-buyer-spec; then
    echo "[ERROR] adapt-buyer-spec failed"
    exit 1
fi

# Run lint_buyer_spec.py and verify exit 0
echo "  → Running lint_buyer_spec.py..."
if ! lint_buyer_spec.py; then
    echo "[ERROR] lint_buyer_spec.py failed (exit non-zero)"
    exit 1
fi

echo "[STEP 8/8] ✓ End-to-end pipeline passed"

# -----------------------------------------------------------------------------
# DONE: Report output location
# -----------------------------------------------------------------------------
echo ""
echo "=========================================="
echo "[SOP] DONE — buyer.tar.gz ready"
echo "=========================================="
echo ""
echo "Summary of what was built:"
echo "  • Python environment with test dependencies"
echo "  • Mineflayer npm packages installed"
echo "  • Paper 1.20.4 Minecraft server (running on :25565)"
echo "  • 1801 placeholder EXR files in placeholders/depth/"
echo "  • Validated buyer spec tarball (buyer.tar.gz)"
echo ""
echo "Output location: $REPO_ROOT/buyer.tar.gz"
echo ""
