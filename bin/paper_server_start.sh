#!/usr/bin/env bash
# paper_server_start.sh — boot Paper with tuned config that prevents bot
# keepalive timeouts. Aikar's flags + low view-distance + no monsters.
#
# USAGE:
#   bin/paper_server_start.sh \
#     --paper-jar /path/to/paper.jar \
#     --server-dir /path/to/srv \
#     [--xmx 4G] [--port 25565] [--view-distance 4]
#
# EXITS: 0 ok, 1 missing arg, 2 jar/dir not found, 3 server didn't reach LISTEN.
#
# Howard 2026-05-06: replaces the inline Paper boot in buyer_spec_pipeline.sh
# that was lacking Aikar's flags and getting "op_full kicked due to keepalive
# timeout" after 45 seconds (Paper "Can't keep up! Running 15401ms behind").

set -euo pipefail

PAPER_JAR=""
SERVER_DIR=""
XMX="4G"
XMS="2G"
PORT="25565"
VIEW_DIST="4"

usage() {
    cat <<USAGE
paper_server_start.sh — boot Paper with tuned anti-keepalive-timeout config.

Required:
  --paper-jar PATH         Path to paper-X.Y.Z.jar
  --server-dir PATH        Working directory for the server (created if absent)

Optional:
  --xmx SIZE               JVM max heap (default 4G)
  --port N                 Server port (default 25565)
  --view-distance N        Chunk view radius (default 4 — keeps load light)

Exits:
  0 server LISTENing on --port within 60s
  1 missing required arg
  2 paper-jar or server-dir parent missing
  3 server did not reach LISTEN within 60s
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --paper-jar)      PAPER_JAR="$2"; shift 2;;
        --server-dir)     SERVER_DIR="$2"; shift 2;;
        --xmx)            XMX="$2"; shift 2;;
        --port)           PORT="$2"; shift 2;;
        --view-distance)  VIEW_DIST="$2"; shift 2;;
        --help|-h)        usage; exit 0;;
        *)                echo "unknown flag: $1" >&2; usage >&2; exit 1;;
    esac
done

if [[ -z "$PAPER_JAR" || -z "$SERVER_DIR" ]]; then
    echo "ERROR: --paper-jar and --server-dir are required" >&2
    usage >&2
    exit 1
fi

if [[ ! -f "$PAPER_JAR" ]]; then
    echo "ERROR: paper-jar not found: $PAPER_JAR" >&2
    exit 2
fi

# Find Java 21 — try PATH first, then common Homebrew location.
if command -v java >/dev/null 2>&1 && java -version 2>&1 | grep -q '"21'; then
    JAVA_BIN="java"
elif [[ -x "/opt/homebrew/opt/openjdk@21/bin/java" ]]; then
    JAVA_BIN="/opt/homebrew/opt/openjdk@21/bin/java"
elif [[ -x "/usr/lib/jvm/java-21-openjdk-amd64/bin/java" ]]; then
    JAVA_BIN="/usr/lib/jvm/java-21-openjdk-amd64/bin/java"
else
    echo "ERROR: Java 21 not found. Install openjdk@21 (brew or apt)." >&2
    exit 2
fi

mkdir -p "$SERVER_DIR"
cd "$SERVER_DIR"

# Copy paper.jar in (avoid path issues)
if [[ ! -f paper.jar ]] || ! cmp -s "$PAPER_JAR" paper.jar 2>/dev/null; then
    cp "$PAPER_JAR" paper.jar
fi

# eula
echo "eula=true" > eula.txt

# server.properties — keep load light
cat > server.properties <<PROPS
online-mode=false
server-port=${PORT}
gamemode=creative
spawn-protection=0
view-distance=${VIEW_DIST}
simulation-distance=${VIEW_DIST}
white-list=false
allow-flight=true
spawn-monsters=false
spawn-animals=false
generate-structures=false
motd=Oyster Pipeline (paper_server_start.sh)
max-players=4
PROPS

# Aikar's flags (https://docs.papermc.io/paper/aikars-flags) for Paper 1.20.4
AIKAR_FLAGS=(
    -Xms"$XMS" -Xmx"$XMX"
    -XX:+UseG1GC
    -XX:+ParallelRefProcEnabled
    -XX:MaxGCPauseMillis=200
    -XX:+UnlockExperimentalVMOptions
    -XX:+DisableExplicitGC
    -XX:+AlwaysPreTouch
    -XX:G1NewSizePercent=30
    -XX:G1MaxNewSizePercent=40
    -XX:G1HeapRegionSize=8M
    -XX:G1ReservePercent=20
    -XX:G1HeapWastePercent=5
    -XX:G1MixedGCCountTarget=4
    -XX:InitiatingHeapOccupancyPercent=15
    -XX:G1MixedGCLiveThresholdPercent=90
    -XX:G1RSetUpdatingPauseTimePercent=5
    -XX:SurvivorRatio=32
    -XX:+PerfDisableSharedMem
    -XX:MaxTenuringThreshold=1
    -Dusing.aikars.flags=https://mcflags.emc.gs
    -Daikars.new.flags=true
)

LOG="${SERVER_DIR}/paper_boot.log"
nohup "$JAVA_BIN" "${AIKAR_FLAGS[@]}" -jar paper.jar nogui > "$LOG" 2>&1 &
PID=$!
echo "Paper PID=$PID, log=$LOG"

# Health check — poll port up to 60s
for i in $(seq 1 60); do
    if (echo > "/dev/tcp/localhost/${PORT}") 2>/dev/null; then
        echo "Paper LISTEN on port ${PORT} after ${i}s"
        echo "$PID" > "${SERVER_DIR}/paper.pid"
        exit 0
    fi
    sleep 1
done

echo "ERROR: Paper did not reach LISTEN within 60s. See $LOG" >&2
tail -20 "$LOG" >&2 || true
exit 3
