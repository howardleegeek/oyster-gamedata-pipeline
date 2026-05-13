# Expert Handoff — Oyster GameData Recorder rc18

**Date**: 2026-05-12
**Current ship**: `recorder-v0.28.0-rc18.0.5` (CI building; same submodule SHA `7bd4d8c` as proven-good rc18.0.3)
**PRD lint score on Howard's test session**: 26/33 = 78.8% after rc18.0.5 finalizer

---

## What this is, in 60 seconds

Windows desktop app that records Minecraft gameplay for AI world-model training. Outputs per PRD §3:

- `recording.mp4` — 1920×1080, 30 fps, 10 Mbps CBR
- `action_camera.json` — per-frame (camera/player position, quaternion, mouse, gamepad, keyCode...) ~29 fields × N frames
- `gameinfo.xlsx` — 14 fields (game name, scene, weather, time_of_day, operator_id, duration, route_type, ...)
- `depth/*.exr` — 6 fps depth maps (currently disabled)

Architecture:
- **Rust+OBS recorder** (`vendor/recorder/`, submodule) — egui tray app, hooks OBS via libobs-wrapper for game-capture, writes mp4 + action_camera + inputs
- **Fabric mc-mod** (`mc-mod/`) — Java mod loaded into bundled Minecraft 1.21.4, writes `game_state.jsonl` (player position, yaw/pitch, dimension, game_mode) every tick
- **Python tooling** (`bin/`) — post-record finalize, PRD lint (32 criteria), acceptance harness
- **Installer** — Inno Setup, bundles JRE + MC + Fabric + mod + recorder.exe into one 859 MB .exe

---

## Three confirmed bugs (priority order for expert)

### Bug 1 — Input capture pipeline drops 100% of events 🔴 HIGH

**File**: `vendor/recorder/crates/input-capture/src/kbm_capture.rs`

**Evidence from a real test session** (`session_20260512_182328_e610fdd6/metadata.json`):
```json
input_capture_diagnostics: {
    registration_tier: "hook",      // hooks DID register
    wm_input_total: 957,            // OS delivered 957 events during 145s
    get_raw_input_data_failures: 0  // GetRawInputData succeeded for all
}
input_stats: {
    total_keyboard_events: 0,       // BUT zero written to inputs.jsonl
    mouse_movement_std: 0.0,
    wasd_apm: 0.0,
    ...
}
```

So events arrive at the Win32 WM_INPUT layer, `GetRawInputData` returns them successfully, but they never make it into `inputs.jsonl`. The pipeline downstream of the LL hook callback is dropping everything.

**Hypothesis** (unverified): the LL hook callback in `kbm_capture.rs` may be filtering by foreground HWND, and the debug log shows the recorder loop sees `Minecraft.exe pid=10492, hwnd=HWND(0x0)` (null window handle) for the actual game process — so HWND-based filtering would drop ALL events. Real MC launches javaw.exe (launcher, hooked OK for video capture) → spawns Minecraft.exe (the game, hwnd=0).

**Expert question**: in `kbm_capture.rs`, is there an HWND filter that needs an "any non-recorder window" fallback when the detected game's HWND is 0? Or is there a thread-pumping issue (LL hooks need a message pump on the install thread)?

**Update — full chain traced statically (2026-05-12 evening)**:

The hook → JSONL pipeline has been fully mapped. Events ARE arriving at the tokio thread but get dropped in one of TWO chokepoints:

```
LL hook (kbm_capture.rs:350)            ─[bumps wm_input_total=957]→
HOOK_EVENT_TX.send                      → hook_rx
hook_rx drained by run_queue            (lib.rs:124 closure does `input_tx.blocking_send(event)`)
input_rx.recv()                         (tokio_thread.rs:224, in tokio::select!)
        │
        ▼  [CHOKE POINT A]
debouncer.debounce(e)                   (tokio_thread.rs:232 — if returns false, `continue;` drops event)
        │
        ▼
state.on_input(e).await                 (tokio_thread.rs:1070)
        │
        ▼
self.recorder.seen_input(e).await       (recorder.rs:296)
        │
        ▼  [CHOKE POINT B]
InputEventType::from_input_event(e)?    (if returns Err, `?` propagates; tokio_thread logs
                                         "Failed to seen input" then continues — event dropped)
        │
        ▼
recording.input_stream().send(...)      (would write to inputs.jsonl)
```

**`seen_input` itself is clean** — no HWND filter, no extra logic. The drop is upstream:

- **Choke A — `EventDebouncer::debounce`** (`src/system/raw_input_debouncer.rs`): if this returns `false` for normal events (over-aggressive bounce window or a recent refactor that mishandles WM_KEYDOWN/UP transitions), ALL 957 events get `continue`-ed away. Look at the debounce algorithm and the window/threshold.

- **Choke B — `InputEventType::from_input_event`** (`src/output_types/mod.rs` or similar): if this returns `Err` for a specific Event variant that's the common case, every event errors → logged → dropped. Look for any unhandled match arm or recent enum-variant addition.

**Verification quickest path**: `grep -nE 'pub fn from_input_event' src/output_types/` to see the conversion match — if there's a `_ => bail!(...)` catchall hit by a common variant, that's Choke B. Otherwise inspect debouncer state.

---

### Bug 2 — mc-mod IPC path mismatch 🟡 MEDIUM (workaround shipped in rc18.0.5)

**Files**:
- `mc-mod/src/main/java/world/oyster/recorder/SessionDir.java` (line 45: `OYSTER_SESSION_DIR` env var)
- `bin/build_bundled_installer/launch_mc.bat` template (no env var set)

**Symptom**: `gameinfo.xlsx` and `game_state.jsonl` missing from recorded session.

**Root cause** (confirmed): mc-mod reads `System.getenv("OYSTER_SESSION_DIR")`; if unset, falls back to `~/Documents/OysterClips/active_session/game_state.jsonl`. The bundled `launch_mc.bat` doesn't set this env var, so mc-mod writes to its fallback path, which the recorder doesn't read. **mc-mod IS running correctly — wrote 3,079 game state samples in Howard's test — they just land at the wrong path.**

**Workaround in rc18.0.5**: `bin/finalize_session.py` syncs the file from fallback path to session dir post-record + backfills `action_camera.json` quaternion/position from it.

**The real fix** (not yet shipped): pass `OYSTER_SESSION_DIR=<recorder-active-session-path>` from recorder → launch_mc.bat → JVM at record-start. This requires either: (a) recorder writes a marker file with its session path that launch_mc.bat reads; (b) recorder spawns MC directly (currently doesn't — user launches MC via tray icon); (c) launch_mc.bat parses recorder logs.

**Expert question**: best Win32-friendly IPC pattern for recorder→bat→JVM env var passthrough at the moment-of-game-launch?

---

### Bug 3 — Lint #13 (Quaternion xyzw Order) heuristic flaw 🟢 LOW

**File**: `bin/lint_v3_prd_grounded.py:651` (`_quat_rest_state_xyzw`)

**Current logic**: vote xyzw if `abs(q).index(max(abs(q))) == 3` (i.e., the last component is the largest absolute value).

**Why this fails on real game data**: in first-person Minecraft with yaw rotation > 90°, w = cos(yaw/2) is small and a non-w component dominates. The heuristic mis-classifies correct xyzw data as wxyz. Howard's session: 90 xyzw votes vs 202 wxyz votes — the heuristic guesses wrong.

**My finalizer produces mathematically correct xyzw** (ZYX intrinsic Euler-to-quat, normalized to |q|=1, verified). Lint #14 passes (norm check); #13 fails on heuristic.

**Expert question**: better xyzw-order heuristic for game-domain quaternions? Or accept this is a known-flawed test that needs a customer-facing replacement?

---

## Two deferred items (rc19+)

### Depth EXR (PRD §3.4)
**Status**: disabled in rc17.3.1 submodule. Cluster's rc17.4 attempt used `capture_screen()` POST-recording to grab desktop frames (wrong — captures whatever's on screen, not the recorded gameplay) and ran at 1 Hz instead of PRD-required 6 Hz. Needs proper rewrite: probably cv2-based mp4 re-decoding + DepthAnything V2 Small (onnxruntime-directml) inference per frame.

### H.265 vs H.264 encoder
**Symptom**: metadata shows `encoder: "x264"` (= H.264), but PRD §3.1 calls for H.265. AMD Radeon 780M GPU on test rig (minipc1) — libobs probed for AMF HEVC → didn't find it → fell back to x264. Need either: (a) GPU-specific configuration; (b) PRD acceptance of x264 fallback when HEVC unavailable.

---

## What the current 8 lint failures attribute to

| # | Failed | Root cause | Owner |
|---|---|---|---|
| 2 | Video duration 145s < 300s | User recorded too briefly | User action |
| 13 | xyzw order heuristic | Lint v3 flaw on large rotations | Bug 3 |
| 14 | Quaternion normalization | ~~no data~~ FIXED rc18.0.5 | ✅ |
| 15 | Depth invalid-pixel | Depth disabled | rc19 |
| 16 | Depth data quality | Depth disabled | rc19 |
| 24 | Directory structure (missing `depth/`) | Depth disabled | rc19 |
| 27 | Inputs.jsonl quality | Empty events | Bug 1 (passes by file-presence; quality is bad) |
| 31 | Mouse/camera alignment | All 50 sampled pairs stationary (mouse_dx=0) | Bug 1 |
| 38 | Audio continuity | Real audio gap > 2s in recording | Hardware-side (minipc1 audio device) |

**Realistic ceiling without rc19**: ~29/33 = 88% (fix Bug 1, longer recording, audio hardware OK).
**Real 100% needs**: Bug 1 fixed + depth EXR shipped + #13 heuristic improved + customer RFC-001 signed.

---

## Specific files an expert could open right now

- **Input pipeline bug**: `vendor/recorder/crates/input-capture/src/kbm_capture.rs` (lines 264+ low-level hook callback)
- **mc-mod env var**: `mc-mod/src/main/java/world/oyster/recorder/SessionDir.java:45` + `bin/build_bundled_installer/installer.iss` (search for `launch_mc.bat` and the bundled bat template)
- **Lint heuristic**: `bin/lint_v3_prd_grounded.py:651` (`_quat_rest_state_xyzw`)
- **Recorder loop / HWND detection**: `vendor/recorder/src/record/recorder.rs` (search "Found running game via process scan")
- **action_camera writer**: `vendor/recorder/src/record/action_camera_writer.rs`
- **rc18.0.5 finalizer**: `bin/finalize_session.py` (workaround for Bug 2)

## What's NOT a problem

- Video capture: ✅ works (10.6 Mbps CBR, OpenGL shared-texture hook on javaw, 174 MB / 145s)
- mc-mod loading: ✅ works (wrote 3,079 ticks in Howard's session — just to wrong path)
- OBS embedding: ✅ no separate OBS process, all in-recorder
- Game detection: ✅ hooks within 2.2s of javaw startup
- Installer: ✅ 859 MB single-file, Inno Setup, includes JRE+MC+Fabric+mods+recorder
- CI pipeline: ✅ 4 workflows (Rust EXE / Python EXE / MC mod / Bundler), works when submodule compiles cleanly

## Repo

`https://github.com/howardleegeek/oyster-gamedata-pipeline` (parent, has `vendor/recorder/` submodule pointing at `gamedata-recorder` repo)

Branch with rc18.x line: `stream-rc17.4-form` (yes, naming is legacy; rc18 happened to fork from it).
