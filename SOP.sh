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
pip install -e .[test,exr,xlsx] >/dev/null 2>&1
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
# STEP 7/8: Stage placeholder farm (1801 real EXR hardlinks + gameinfo.xlsx)
# -----------------------------------------------------------------------------
echo "[STEP 7/8] Staging placeholder farm..."

PLACEHOLDER_DIR="$REPO_ROOT/placeholders/depth"
mkdir -p "$PLACEHOLDER_DIR"
SEED_FILE="$PLACEHOLDER_DIR/frame_seed.exr"

if [[ ! -f "$SEED_FILE" ]]; then
    "$REPO_ROOT/.venv/bin/python" - << EOF
import numpy as np, OpenEXR, Imath
W, H = 96, 96
xs = np.linspace(0.5, 30.0, W, dtype=np.float32)
ys = np.linspace(0.5, 30.0, H, dtype=np.float32)
depth = ((xs[None,:] + ys[:,None]) / 2.0).astype(np.float32)
hdr = OpenEXR.Header(W, H)
hdr["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
exr = OpenEXR.OutputFile("$SEED_FILE", hdr)
exr.writePixels({"Z": depth.tobytes()})
exr.close()
EOF
fi

# Hardlink 1800 copies named frame_NNNNNN.exr
EXR_COUNT=$(ls "$PLACEHOLDER_DIR"/frame_*.exr 2>/dev/null | wc -l | tr -d " ")
if [[ "$EXR_COUNT" -lt 1800 ]]; then
    for i in $(seq 0 1799); do
        printf -v fn "frame_%06d.exr" "$i"
        ln -f "$SEED_FILE" "$PLACEHOLDER_DIR/$fn"
    done
fi

# Generate gameinfo.xlsx if missing
if [[ ! -f "$REPO_ROOT/placeholders/gameinfo.xlsx" ]]; then
    "$REPO_ROOT/.venv/bin/python" - << EOF
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "metadata"
ws["A1"] = "field"
ws["B1"] = "value"
ws["A2"] = "game_name"
ws["B2"] = "Minecraft"
ws["A3"] = "fps"
ws["B3"] = 30.0
ws["A4"] = "width"
ws["B4"] = 1920
ws["A5"] = "height"
ws["B5"] = 1080
wb.save("$REPO_ROOT/placeholders/gameinfo.xlsx")
EOF
fi

echo "  ✓ EXR farm: $(ls $PLACEHOLDER_DIR/frame_*.exr | wc -l | tr -d " ") files at $PLACEHOLDER_DIR"
echo "  ✓ gameinfo.xlsx at $REPO_ROOT/placeholders/gameinfo.xlsx"
echo "[STEP 7/8] ✓ Placeholder farm staged"

# -----------------------------------------------------------------------------
# STEP 8/8: Run end-to-end pipeline
# -----------------------------------------------------------------------------
echo "[STEP 8/8] Running end-to-end pipeline..."

# Use the Aliyun-tested e2e_smoke.sh which has all the right flags
echo "  → Running bin/e2e_smoke.sh (capture → adapt → lint)..."
PLACEHOLDERS="$REPO_ROOT/placeholders" bash "$REPO_ROOT/bin/e2e_smoke.sh"

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
