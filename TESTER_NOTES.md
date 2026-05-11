# Oyster Recorder — Tester Notes (rc16.x)

## What's new in rc16

The recorder now ships **two engines** in one installer:

1. **Rust+OBS recorder** (primary) — uses OBS Studio's battle-tested screen
   capture, lives at `{install_root}/recorder/OysterRecorder.exe`
2. **Python recorder** (fallback) — the rc15.31 recorder with mss/ddagrab/
   gdigrab capture tiers, lives at `{install_root}/OysterRecorder-onedir/`

By default the launcher tries Rust first. If the Rust binary is missing
(e.g. installer corruption), the launcher automatically falls back to
the Python recorder.

## If recording isn't working

The most likely cause on unusual hardware (AMD iGPU, virtualized graphics,
Windows-on-ARM, emulator-hosted Minecraft) is that OBS Studio's D3D11 capture
path can't initialize. Symptoms include:

- Recorder window opens then closes immediately
- Recording starts but `video.mp4` has only 1 visible frame
- No recording file produced at all

**To force the Python fallback engine instead of Rust+OBS:**

### Option A — for one session

Open Command Prompt (Win+R, type `cmd`, Enter) and run:

```
set OYSTER_PY_RECORDER=1
"C:\Program Files\Oyster Recorder\OysterPlay.exe"
```

### Option B — permanently

1. Press `Win+R`, type `sysdm.cpl`, press Enter
2. Click "Advanced" tab → "Environment Variables…"
3. Under "User variables", click "New…"
4. Variable name: `OYSTER_PY_RECORDER`
5. Variable value: `1`
6. Click OK three times. The change takes effect on the next launch.

Accepted values for the env var: `1`, `true`, `yes`, `on` (case-insensitive).

## Reporting back

If the Python fallback also produces 1-frame video or no recording, that's
important data. Send us:

1. The diagnostic ZIP (right-click the system-tray icon → "Save diagnostic")
2. Which engine you were running (env var set or not)
3. Roughly how long the launcher took to show the Minecraft window

This tells us whether the bug is in the recording engine or somewhere else
in the pipeline.
