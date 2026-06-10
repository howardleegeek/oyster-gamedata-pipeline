<!--
G183 · docs/runbooks/STARDEW_RUNBOOK.md
Operator runbook for Stardew Valley capture: SMAPI install + mod copy + 30-min smoke.
Mirrors BEAMNG_RUNBOOK structure and conventions.
-->

# Stardew Valley Capture Runbook

## Overview

Operator runbook for capturing Stardew Valley gameplay data using SMAPI
(Stardew Modding API) and our custom capture mod.

**Target Audience:** Operations engineers, QA testers  
**Estimated Time:** 45-60 minutes  
**Prerequisites:** Stardew Valley installed, admin/sudo access

---

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Windows 10, macOS 10.14+, Ubuntu 18.04+ | Windows 11, macOS 13+, Ubuntu 22.04+ |
| RAM | 4 GB | 8 GB |
| Disk Space | 2 GB free | 5 GB free |
| Stardew Valley | Version 1.5.6+ | Latest stable |

### Required Files

- `SMAPI_<version>-installer.zip` - SMAPI installer
- `capture_mod_<version>.zip` - Custom capture mod
- `capture_config.yaml` - Capture configuration

---

## Environment Setup

### Step 1: Verify Game Installation

```bash
# Linux/macOS
ls -la ~/GOG\ Games/Stardew\ Valley/game/Stardew\ Valley
ls -la ~/.steam/steam/steamapps/common/Stardew\ Valley

# Windows (PowerShell)
Test-Path "C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"
```

### Step 2: Create Working Directory

```bash
# Linux/macOS
export STARDEW_WORKDIR="$(mktemp -d -t stardew_capture_XXXXXX)"
export STARDEW_BACKUP_DIR="${STARDEW_WORKDIR}/backup"
mkdir -p "${STARDEW_BACKUP_DIR}"

# Windows (PowerShell)
$env:STARDEW_WORKDIR = New-TemporaryFile | ForEach-Object { 
    Remove-Item $_; New-Item -ItemType Directory -Path $_ 
}
$env:STARDEW_BACKUP_DIR = Join-Path $env:STARDEW_WORKDIR "backup"
New-Item -ItemType Directory -Path $env:STARDEW_BACKUP_DIR
```

### Step 3: Set Environment Variables

```bash
export STARDEW_GAME_DIR="/path/to/Stardew Valley"
export STARDEW_MODS_DIR="${STARDEW_GAME_DIR}/Mods"
export SMAPI_VERSION="3.18.6"
export CAPTURE_MOD_VERSION="1.0.0"
```

---

## SMAPI Installation

### Step 1: Download and Extract SMAPI

```bash
curl -L -o "${STARDEW_WORKDIR}/SMAPI-installer.zip" \
    "https://github.com/Pathoschild/SMAPI/releases/download/${SMAPI_VERSION}/SMAPI-${SMAPI_VERSION}-installer.zip"
cd "${STARDEW_WORKDIR}" && unzip SMAPI-installer.zip -d smapi_installer
```

### Step 2: Run Installer

```bash
# Linux/macOS
cd smapi_installer && chmod +x install.sh
./install.sh --no-prompt --game-path "${STARDEW_GAME_DIR}"

# Windows (run as Administrator)
./install.exe --no-prompt --game-path "C:\Games\Stardew Valley"
```

### Step 3: Verify Installation

```bash
ls -la "${STARDEW_GAME_DIR}/StardewModdingAPI"
"${STARDEW_GAME_DIR}/StardewModdingAPI" --version
```

---

## Mod Deployment

### Step 1: Backup Existing Mods

```bash
if [ -d "${STARDEW_MODS_DIR}" ]; then
    cp -r "${STARDEW_MODS_DIR}" "${STARDEW_BACKUP_DIR}/Mods_backup_$(date +%Y%m%d_%H%M%S)"
fi
```

### Step 2: Deploy Capture Mod

```bash
mkdir -p "${STARDEW_MODS_DIR}"
unzip "${STARDEW_WORKDIR}/capture_mod_${CAPTURE_MOD_VERSION}.zip" -d "${STARDEW_MODS_DIR}"
```

### Step 3: Configure and Verify

```bash
cp capture_config.yaml "${STARDEW_MODS_DIR}/CaptureMod/config.yaml"
"${STARDEW_GAME_DIR}/StardewModdingAPI" --list-mods
```

Expected output should include `CaptureMod` with status `OK`.

---

## Plug-and-Play Relay Contract

The CI adapter uses `StardewValleyEnvironment` in
`src/oyster_agent_runner/environments/stardew_valley.py`. It preserves the
existing `SMAPIRelayClient` and wraps it with the shared Environment protocol.

### Required Relay Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/state` | Return the current player/map/key state |
| `POST` | `/press` | Apply one input action: `{"key": "right"}` |

`GET /state` must return JSON fields compatible with:

```json
{
  "timestamp": 1779469000.0,
  "map": "Farm",
  "x": 12.5,
  "y": 44.0,
  "facing": "down",
  "keys": {
    "up": false,
    "down": false,
    "left": false,
    "right": false,
    "use_tool": false,
    "do_action": false,
    "cancel": false,
    "run": false
  }
}
```

The adapter emits runner observations with exactly these stable fields:

```json
{
  "timestamp": 1779469000.0,
  "map_name": "Farm",
  "player_position": {"x": 12.5, "y": 44.0},
  "facing": "down",
  "keys": {
    "up": false,
    "down": false,
    "left": false,
    "right": false,
    "use_tool": false,
    "do_action": false,
    "cancel": false,
    "run": false
  },
  "source": "smapi_relay"
}
```

### Actions

Only these actions are accepted by `step(action)`:

- `up`
- `down`
- `left`
- `right`
- `use_tool`
- `do_action`
- `cancel`
- `run`
- `noop`

Accepted action shapes are `{"key": "right"}`, `{"action": "right"}`, or
`{"op": "right"}`. `noop` refreshes `/state` without sending `/press`.
Every non-`noop` action posts to `/press` first, then reads `/state` so the
observation is post-action state.

### CI Smoke

Run the plug-and-play contract tests before shipping a Stardew adapter change:

```bash
python3 -m pytest tests/test_stardew_valley_env.py tests/test_game_plugins.py -q
ruff check src/oyster_agent_runner/environments/stardew_valley.py tests/test_stardew_valley_env.py
black --check src/oyster_agent_runner/environments/stardew_valley.py tests/test_stardew_valley_env.py
```

---

## 30-Minute Smoke Test

### Step 1: Start Game with Logging

```bash
cd "${STARDEW_GAME_DIR}"
./StardewModdingAPI --log-path "${STARDEW_WORKDIR}/smoke_test.log" 2>&1 | \
    tee "${STARDEW_WORKDIR}/console.log" &
GAME_PID=$!
```

### Step 2: Monitor Startup (5 minutes)

```bash
tail -f "${STARDEW_WORKDIR}/smoke_test.log" | grep -E "(CaptureMod|loaded|error|warning)"
```

Expected log entries:

- `[SMAPI] Mods loaded: X`
- `[CaptureMod] Initialized successfully`
- `[CaptureMod] Recording started`

### Step 3: Gameplay Verification (20 minutes)

| Action | Duration | Verify |
|--------|----------|--------|
| Load/create save | 2 min | No crashes, mod UI visible |
| Farm activities | 5 min | Data capture logs present |
| NPC interactions | 5 min | Event data recorded |
| Menu navigation | 3 min | UI state captured |
| Save and reload | 5 min | Data persists correctly |

### Step 4: Verify Capture Data (5 minutes)

```bash
ls -la "${STARDEW_MODS_DIR}/CaptureMod/output/"
find "${STARDEW_MODS_DIR}/CaptureMod/output/" -type f -mmin -30 | wc -l
```

Expected: Multiple `.json` or `.csv` files with recent timestamps.

### Step 5: Stop Game and Archive

```bash
kill -SIGTERM ${GAME_PID} 2>/dev/null || true
wait ${GAME_PID} 2>/dev/null || true

tar -czf "${STARDEW_WORKDIR}/smoke_test_logs.tar.gz" \
    "${STARDEW_WORKDIR}/smoke_test.log" \
    "${STARDEW_WORKDIR}/console.log" \
    "${STARDEW_MODS_DIR}/CaptureMod/output/"
```

---

## Troubleshooting

### SMAPI Installation Fails

1. Verify game directory permissions: `ls -la "${STARDEW_GAME_DIR}"`
2. Check for existing SMAPI: `ls -la "${STARDEW_GAME_DIR}/StardewModdingAPI"`
3. Run installer with verbose logging: `./install.sh --verbose`

### Mod Not Loading

1. Verify mod directory structure: `ls -la "${STARDEW_MODS_DIR}/CaptureMod/"`
2. Check manifest.json exists: `cat "${STARDEW_MODS_DIR}/CaptureMod/manifest.json"`
3. Review SMAPI error logs: `grep -i error "${STARDEW_GAME_DIR}/error-log.txt"`

### No Capture Data

1. Verify config.yaml syntax: `python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"`
2. Check disk space: `df -h "${STARDEW_MODS_DIR}"`
3. Review mod logs: `grep -i "CaptureMod.*error" "${STARDEW_WORKDIR}/smoke_test.log"`

### Game Crashes During Test

1. Check crash logs: `cat "${STARDEW_GAME_DIR}/crash-log.txt"`
2. Verify mod compatibility with SMAPI version
3. Disable other mods and retest

---

## Rollback Procedure

```bash
# Stop game
pkill -f StardewModdingAPI || true

# Remove capture mod
rm -rf "${STARDEW_MODS_DIR}/CaptureMod"

# Restore backup (if exists)
if [ -d "${STARDEW_BACKUP_DIR}/Mods_backup_"* ]; then
    rm -rf "${STARDEW_MODS_DIR}"
    cp -r "${STARDEW_BACKUP_DIR}/Mods_backup_"* "${STARDEW_MODS_DIR}"
fi
```

---

## Cleanup

```bash
# Archive results to permanent storage
cp "${STARDEW_WORKDIR}/smoke_test_logs.tar.gz" \
    "/path/to/results/stardew_capture_$(date +%Y%m%d_%H%M%S).tar.gz"

# Remove temporary files
rm -rf "${STARDEW_WORKDIR}"
```

---

## Checklist

- [ ] Game installation verified
- [ ] Working directory created
- [ ] SMAPI installed and verified
- [ ] Capture mod deployed
- [ ] Configuration applied
- [ ] 30-minute smoke test completed
- [ ] Capture data verified
- [ ] Logs archived
- [ ] Cleanup performed

---

## Contact

For issues with this runbook, contact the Platform Engineering team or
consult the BEAMNG_RUNBOOK for similar procedures and conventions.
