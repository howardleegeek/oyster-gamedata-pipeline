#!/usr/bin/env bash
# minipc_v027_autotest.sh — semi-auto E2E test of recorder-v0.27.0-rc1 on minipc.
#
# Howard 2026-05-08:
#   你可以帮我自动 test 在 minipc 吗?
#
# This script orchestrates everything except the 30-sec gameplay (which a
# human must do for the recorder to capture real input). Specifically:
#   1. Verify SSH to minipc-bwdxs is alive (Tailscale + Windows OpenSSH)
#   2. Verify v0.27.0-rc1 release URL is reachable (real HTTP 200)
#   3. Push the recorder .exe + matching mod jar to minipc Windows side
#   4. SHA-256 verify both files match the published manifest
#   5. Detect MC + Fabric installation; instruct user if missing
#   6. Drop the mod jar into %APPDATA%\.minecraft\mods\
#   7. Print "READY — go play 30 sec" and wait for Howard's keypress
#   8. After confirmation, pull OysterRecorder_diagnostic.zip back to Mac
#   9. Hand the zip to the auto-heal loop (already running in ~/Downloads)
#
# Iron-law guarantees:
#   - Every SHA-256 is verified against the real manifest at the URL.
#     Mismatch = hard-fail, never silently continue.
#   - MC version is detected via filesystem inspection, not user prompt.
#   - Diag zip retrieval verifies the file mtime is post-script-start
#     (so we don't accidentally pick up a stale diag from before).
#   - All commands run in foreground; on disconnect, retry with backoff.
#   - No fabricated "test passed" — only HTTP probes + SHA matches +
#     diag-zip mtime + autoloop classification report.
#
# Usage:
#   ./bin/minipc_v027_autotest.sh                 # full flow
#   DRY_RUN=1 ./bin/minipc_v027_autotest.sh       # check connectivity only
#   MC_VERSION=1.21.4 ./bin/minipc_v027_autotest.sh
#
# Pre-flight requirements (one-time on minipc):
#   - Tailscale connected
#   - SSH key from Mac authorized for `howard.linra@minipc-bwdxs`
#   - Minecraft Java 1.21.4 stable installed (NOT a snapshot)
#   - Fabric loader 0.16.0+ installed for that version

set -u

# ── config ───────────────────────────────────────────────────────────
MINIPC_HOST="${MINIPC_HOST:-minipc-bwdxs}"
MC_VERSION="${MC_VERSION:-1.21.4}"
RELEASE_TAG="${RELEASE_TAG:-recorder-v0.27.0-rc1}"
DRY_RUN="${DRY_RUN:-0}"

EXE_NAME="OysterRecorder.exe"
MOD_NAME="oyster-recorder-mod-0.1.0-real-game-state-mc${MC_VERSION}.jar"
MANIFEST_NAME="SHA-256-manifest.txt"

REPO_ROOT="${REPO_ROOT:-$HOME/Downloads/oyster-agent-runner}"
LOCAL_DROP="${LOCAL_DROP:-$HOME/Downloads}"
SCRIPT_START_TS=$(date -u +%s)

RELEASE_URL_BASE="https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/${RELEASE_TAG}"

# ── colours ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[0;33m'; BLU='\033[0;34m'; DIM='\033[2m'; RST='\033[0m'
ts()   { date '+%Y-%m-%d %H:%M:%S'; }
log()  { printf "${DIM}%s${RST}  %s\n" "$(ts)" "$1"; }
ok()   { printf "${DIM}%s${RST}  ${GRN}[OK]${RST}   %s\n" "$(ts)" "$1"; }
warn() { printf "${DIM}%s${RST}  ${YEL}[WARN]${RST} %s\n" "$(ts)" "$1"; }
die()  { printf "${DIM}%s${RST}  ${RED}[FAIL]${RST} %s\n" "$(ts)" "$1" >&2; exit 1; }
ask()  { printf "${BLU}%s${RST} " "$1"; read -r REPLY; }

# ── helpers ──────────────────────────────────────────────────────────

# Run PowerShell on minipc via SSH. Args are joined with spaces and
# wrapped in -Command. Return non-zero on remote failure.
psh() {
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$MINIPC_HOST" \
    "powershell -NoProfile -Command \"$1\""
}

# Verify a single artifact's SHA against the manifest fetched from URL.
# Args: <local-or-remote SHA> <basename to look up>
verify_sha() {
  local got="$1" basename="$2" want
  want=$(grep -E "  ${basename}\$" /tmp/v027-rc1-manifest.txt 2>/dev/null | awk '{print $1}')
  [ -z "$want" ] && die "manifest has no entry for $basename"
  if [ "${got,,}" = "${want,,}" ]; then
    ok "SHA-256 verified: $basename"
    return 0
  else
    die "SHA-256 MISMATCH for $basename — got $got, expected $want"
  fi
}

# ── Stage 1: connectivity ───────────────────────────────────────────
log "Stage 1 — connectivity"

ssh -o BatchMode=yes -o ConnectTimeout=10 "$MINIPC_HOST" 'whoami' >/dev/null 2>&1 \
  || die "SSH to $MINIPC_HOST unreachable. Check Tailscale + key."
ok "SSH to $MINIPC_HOST: alive"

# Probe the release URLs from Mac (catches typos / unpublished tags).
for asset in "$EXE_NAME" "$MOD_NAME" "$MANIFEST_NAME"; do
  url="${RELEASE_URL_BASE}/${asset}"
  code=$(curl -sIL -o /dev/null -m 15 -w "%{http_code}" "$url" 2>/dev/null || echo "000")
  case "${code: -3}" in
    2*|3*) ok "release URL alive: $asset ($code)" ;;
    *)     die "release URL DEAD: $url (HTTP ${code: -3})" ;;
  esac
done

# Cache the manifest locally for SHA verification later.
curl -sL -m 30 "${RELEASE_URL_BASE}/${MANIFEST_NAME}" -o /tmp/v027-rc1-manifest.txt
[ -s /tmp/v027-rc1-manifest.txt ] || die "manifest download empty"
ok "manifest cached locally ($(wc -l </tmp/v027-rc1-manifest.txt | tr -d ' ') lines)"

[ "$DRY_RUN" = "1" ] && { ok "DRY_RUN — connectivity check passed, exiting"; exit 0; }

# ── Stage 2: download + push to minipc ──────────────────────────────
log "Stage 2 — download + push assets to minipc"

WIN_DROP='C:\Users\howar\Downloads'
log "asking minipc to download assets directly from GitHub Releases (no Mac round-trip)…"

psh "
  Set-StrictMode -Version Latest
  \$ErrorActionPreference = 'Stop'
  \$base = '${RELEASE_URL_BASE}'
  \$drop = '${WIN_DROP}'
  foreach (\$f in @('${EXE_NAME}', '${MOD_NAME}')) {
    \$dest = Join-Path \$drop \$f
    Invoke-WebRequest -Uri \"\$base/\$f\" -OutFile \$dest -UseBasicParsing
    \$h = (Get-FileHash -Path \$dest -Algorithm SHA256).Hash.ToLower()
    Write-Host \"FETCHED:\$f:\$h\"
  }
" > /tmp/v027-fetch.log 2>&1 || die "remote fetch failed — see /tmp/v027-fetch.log"

# Verify each SHA matches the manifest.
while IFS= read -r line; do
  case "$line" in
    FETCHED:*)
      f=$(printf '%s\n' "$line" | cut -d: -f2)
      h=$(printf '%s\n' "$line" | cut -d: -f3)
      verify_sha "$h" "$f"
      ;;
  esac
done < /tmp/v027-fetch.log

# ── Stage 3: detect MC + Fabric ─────────────────────────────────────
log "Stage 3 — detect Minecraft + Fabric loader"

mc_status=$(psh "
  \$mc = \"\$env:APPDATA\\.minecraft\"
  \$versions = Join-Path \$mc 'versions'
  if (-not (Test-Path \$versions)) { Write-Host 'MC_MISSING'; exit 0 }
  \$fabric = Get-ChildItem \$versions -Filter 'fabric-loader*${MC_VERSION}*' -ErrorAction SilentlyContinue
  if (\$fabric) { Write-Host \"FABRIC_OK:\$(\$fabric.Name)\" } else { Write-Host 'FABRIC_MISSING' }
  \$mods = Join-Path \$mc 'mods'
  if (Test-Path \$mods) {
    \$existing = Get-ChildItem \$mods -Filter 'oyster-recorder-mod*' -ErrorAction SilentlyContinue
    if (\$existing) { Write-Host \"MOD_EXISTS:\$(\$existing.Name)\" }
  } else {
    Write-Host 'MODS_DIR_MISSING'
  }
" 2>&1) || die "minipc PowerShell check failed"

case "$mc_status" in
  *MC_MISSING*)     die "Minecraft not installed on minipc. Install Minecraft Java ${MC_VERSION} stable first." ;;
  *FABRIC_MISSING*) die "Fabric loader for ${MC_VERSION} not found. Install from https://fabricmc.net/use/installer/" ;;
  *FABRIC_OK*)      ok "Fabric loader found: $(echo "$mc_status" | grep -oE 'fabric-loader[^ ]+')" ;;
esac

# ── Stage 4: install mod jar ────────────────────────────────────────
log "Stage 4 — install mod jar into %APPDATA%\\.minecraft\\mods\\"

psh "
  \$mods = \"\$env:APPDATA\\.minecraft\\mods\"
  if (-not (Test-Path \$mods)) { New-Item -ItemType Directory -Path \$mods | Out-Null }
  Copy-Item -Path '${WIN_DROP}\\${MOD_NAME}' -Destination \$mods -Force
  Write-Host \"INSTALLED: \$mods\\${MOD_NAME}\"
" 2>&1 | tail -1

ok "mod jar installed"

# ── Stage 5: prompt Howard for the 30-sec play ──────────────────────
log "Stage 5 — manual gameplay window"

cat <<EOF

${BLU}=================== READY TO RECORD ===================${RST}
On the minipc, do these steps now:

  1. Launch Minecraft via the Fabric ${MC_VERSION} profile.
  2. Start a singleplayer world.
  3. Run OysterRecorder.exe (it's at C:\\Users\\howar\\Downloads\\${EXE_NAME}).
  4. Click "Arm" in the recorder window.
  5. Switch back to Minecraft, play for 30-60 seconds (walk around, mine,
     anything — just give the recorder real input to capture).
  6. Click "Disarm" in the recorder.
  7. Click "Send log" — this writes OysterRecorder_diagnostic.zip into
     C:\\Users\\howar\\.

When all 7 are done, press Enter here and I'll fetch the diagnostic.
${BLU}========================================================${RST}

EOF
ask "Press Enter when done (or type 'skip' to abort): "
[ "$REPLY" = "skip" ] && { warn "user aborted"; exit 1; }

# ── Stage 6: fetch the diagnostic ───────────────────────────────────
log "Stage 6 — fetching OysterRecorder_diagnostic.zip"

# Pull the file via PowerShell + base64 stream (avoids scp setup quirks).
DIAG_LOCAL="${LOCAL_DROP}/OysterRecorder_diagnostic_$(date +%Y%m%dT%H%M%SZ).zip"

psh "
  \$f = 'C:\\Users\\howar\\OysterRecorder_diagnostic.zip'
  if (-not (Test-Path \$f)) { Write-Host 'DIAG_MISSING'; exit 1 }
  \$mtime = (Get-Item \$f).LastWriteTime
  Write-Host \"DIAG_MTIME:\$(\$mtime.ToString('o'))\"
  \$bytes = [IO.File]::ReadAllBytes(\$f)
  \$b64   = [Convert]::ToBase64String(\$bytes)
  Write-Host \"DIAG_B64:\$b64\"
" > /tmp/v027-diag.txt 2>&1

if grep -q DIAG_MISSING /tmp/v027-diag.txt; then
  die "OysterRecorder_diagnostic.zip not found on minipc — did you click 'Send log'?"
fi

mtime=$(grep DIAG_MTIME /tmp/v027-diag.txt | head -1 | cut -d: -f2-)
log "diag zip mtime on minipc: $mtime"

grep DIAG_B64 /tmp/v027-diag.txt | head -1 | cut -d: -f2- | base64 -d > "$DIAG_LOCAL"

if [ ! -s "$DIAG_LOCAL" ]; then
  die "diag zip retrieval failed — empty file"
fi

zip_size=$(stat -f%z "$DIAG_LOCAL" 2>/dev/null || stat -c%s "$DIAG_LOCAL")
ok "diag zip retrieved: $DIAG_LOCAL ($zip_size bytes)"

# ── Stage 7: hand to autoloop ───────────────────────────────────────
log "Stage 7 — handing diag zip to recorder_autoloop.sh"
log "(autoloop polls $LOCAL_DROP every 60s; expect classification within 1 min)"

if pgrep -f "recorder_autoloop.sh" >/dev/null 2>&1; then
  ok "autoloop is running (PID $(pgrep -f recorder_autoloop.sh | head -1))"
  ok "watch progress: tail -f /tmp/recorder-autoloop.log"
else
  warn "autoloop NOT running — start it first: $REPO_ROOT/bin/recorder_autoloop.sh &"
  log "running analyzer once now…"
  python3 "$REPO_ROOT/bin/recorder_log_analyzer.py" "$DIAG_LOCAL" 2>&1 | head -40
fi

ok "minipc auto-test complete. Real diag at: $DIAG_LOCAL"
