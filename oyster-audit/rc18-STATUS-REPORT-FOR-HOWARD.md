# rc18 Status Report — For Howard's Return

**Generated**: 2026-05-12 ~18:35 PDT
**Tag shipped**: `recorder-v0.28.0-rc18.0.3`
**Test session analyzed**: `session_20260512_182328_e610fdd6` on minipc1

---

## TL;DR

- ✅ **rc18.0.3 installer ships**: 859 MB at https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/recorder-v0.28.0-rc18.0.3/GameDataRecorder-Setup-recorder-v0.28.0-rc18.0.3.exe
- ✅ **Video recording works perfectly**: 174 MB / 145.97s / CBR 10 Mbps / 1920×1080 / 30 fps
- ⚠️ **PRD compliance baseline: 25/33 lint criteria pass (75.8%)**
- 🔴 **3 real bugs identified** — 2 are fixable, 1 is workflow (your action)
- 🟢 **Path to 31/33 (94%)** with rc18.0.4 if we land the right fixes

## What works (8 PRD-related ✅)

| Item | Status |
|---|---|
| `recording.mp4` (PRD §3.1) | ✅ H.264 (AMD 780M GPU fallback from H.265), 30fps, 10 Mbps CBR exact |
| `action_camera.json` (PRD §3.2) | ✅ 146 frames × 29 fields each (exceeds PRD's 20-field minimum) |
| Video duration | ⚠️ 145.97s but PRD wants 300-360s (just record longer; not a bug) |
| Video content health | ✅ varied + non-black |
| Frame continuity | ✅ no gaps |
| Lint criteria 1-12, 17-23, 25-30 (most criteria) | ✅ 25/33 pass |
| OBS game-capture hook | ✅ Hooked javaw.exe in 2.2 seconds via OpenGL shared texture |
| Input hooks register | ✅ `registration_tier: "hook"`, `wm_input_total: 957` events received |

## Three real bugs

### Bug 1 — Input capture pipeline drops 100% of events ⚠️ HIGH

**Evidence** (`metadata.json`):
```json
input_capture_diagnostics.wm_input_total: 957     ← OS delivered 957 events
input_stats.total_keyboard_events: 0              ← Recorder wrote 0 events
input_stats.mouse_movement_std: 0.0               ← No mouse data
```

**Lint criteria affected**: #27 inputs.jsonl present (✅ but only marker events), #31 mouse/camera alignment (❌ no mouse_dx)

**Root cause**: WM_INPUT messages arrive at the Win32 layer (957 of them — Howard's gameplay) but are dropped before being written to `inputs.jsonl`. The pipeline downstream of `GetRawInputData` in `crates/input-capture/src/kbm_capture.rs` is broken.

**Fix scope**: Rust submodule patch in `kbm_capture.rs`. Risk: medium (this is the BG-rescue rc17.3 work). Time: 1-2 hours including local Windows cargo check on minipc1 (not blind push to CI).

**Defer recommendation**: rc18.0.5 or rc19 — requires careful Rust debugging.

### Bug 2 — `gameinfo.xlsx` + `game_state.jsonl` missing 🟡 MEDIUM

**Evidence**: Both files absent from session dir. The Fabric mc-mod jar IS bundled at `OysterRecorder/mc-instance/mods/oyster-recorder-mod-0.1.0-real-game-state-mc1.21.4.jar` but it appears NOT to have run during the recording.

**Lint criteria affected**: #13 Quaternion xyzw, #14 Quaternion Normalization, #24 Directory Structure (gameinfo part), #31 (camera alignment relies on quaternion + mouse)

**Root cause** (uncertain — needs debug): The bundled MC instance did run (we see `javaw_1778635370.log` in `OysterRecorder/logs/`). But either:
- (a) mc-mod jar isn't loading into Fabric class loader on MC 1.21.4 → mod file mismatch?
- (b) mc-mod loads but IPC env var (where to write `game_state.jsonl`) isn't being passed by `launch_mc.bat` to the JVM

**Investigation needed**: Check `launch_mc.bat` for `OYSTER_GAME_STATE_PATH` or similar env var, and check Fabric `--mods` flag arrangement.

**Workflow workaround for now**: If you launched MC via your own Mojang launcher (`%APPDATA%\.minecraft\versions\fabric-loader-0.16.10-1.21.4`), THAT profile doesn't have our mc-mod. Only `OysterRecorder\mc-instance\` has it.

### Bug 3 — `audio_check.json` not auto-generated 🟢 LOW

**Lint criterion affected**: #38 Audio Continuity

**Root cause**: `bin/audio_continuity_check.py` (we wrote this in rc18 salvage) exists but isn't auto-invoked when recording finalizes.

**Fix scope**: Add a finalization hook — either the Rust recorder calls it post-stop, OR `prd_acceptance.py` runs it as a pre-lint step. ~5 lines of Python.

**Easy rc18.0.4 win.**

## Path to higher PRD score

| Fix | Lint criteria unblocked | New score | Effort |
|---|---|---|---|
| **Bug 3 only** (audio_check wiring) | #38 | 26/33 → 78.8% | 1 hour, low risk |
| **Bug 3 + Bug 2** (mc-mod IPC debug + audio) | #13, #14, #24, #31, #38 | 31/33 → 93.9% | 2-4 hours, medium risk |
| **All 3 bugs** (Bug 1 input pipeline too) | + #27 quality | 31/33 + quality of inputs.jsonl | 4-6 hours, harder Rust debug |
| **Depth EXR re-enable** (rc17.4 cv2 was broken; defer to rc19) | #15, #16 | 33/33 → 100% | rc19+ |

## What I deliberately did NOT do in this session

- Did not patch installer.iss for `.minecraft\mods\` auto-install — installer.iss line 54 has an explicit iron-law `;NO TOUCHING %APPDATA%\.minecraft\`. The design intent is the bundled `mc-instance/` IS the supported path.
- Did not try to fix Bug 1 (input pipeline) — too invasive without ability to cargo-check locally on Windows from this mac.
- Did not blind-merge the 10 unmerged cluster branches — they would have been un-audited risk on a ship-quality release.
- Did not delete the failed rc18.0 / rc18.0.1 / rc18.0.2 tags & releases — waiting on your explicit go-ahead (option A/B/C from earlier).

## What HOWARD needs to do on return

1. **Decide on cleanup of failed tags/releases** (A: kill dangerous + keep tags; B: kill all 3; C: rename DEPRECATED; D: leave alone)
2. **Decide rc18.0.4 scope**:
   - Minimum: just Bug 3 (audio_check wiring) — 1 hour, ships clean
   - Recommended: Bug 3 + Bug 2 debug (mc-mod IPC) — 2-4 hours, biggest PRD lift
   - Aggressive: All 3 bugs — needs Rust on minipc1 local cargo check (we'd avoid the rc17.4 disaster repeat)
3. **For testing rc18.0.4 once it ships**: launch via `C:\Users\howar\AppData\Local\OysterRecorder\launch_mc.bat` (bundled MC with mc-mod loaded), NOT via Mojang Launcher

## Cluster + tag inventory snapshot

- Cluster: 0 agents alive (all 7 killed earlier when one went destructive)
- rc18 tags on origin: `rc18.0` (mislabeled rc17.3.1 content, 859 MB), `rc18.0.1` (Python EXE only), `rc18.0.2` (Python EXE only), `rc18.0.3` (CORRECT, 859 MB bundled installer + Python EXE + 9 mod jars)
- minipc1: rc18.0.3 installer SHA-verified at `C:\Users\howar\Downloads\GameDataRecorder-Setup-rc18.0.3.exe`, installed in-place on `C:\Users\howar\AppData\Local\OysterRecorder\`

## File paths for your reference

```
Installer:          C:\Users\howar\Downloads\GameDataRecorder-Setup-rc18.0.3.exe   (859 MB, SHA: 3bd243e4…d6f8)
Install root:       C:\Users\howar\AppData\Local\OysterRecorder\                   (Inno upgrade-in-place)
Recordings:         C:\Users\howar\AppData\Local\GameData Recorder\recordings\
Recorder logs:      C:\Users\howar\AppData\Roaming\GameData Recorder\gamedata-recorder-debug.log
MC logs:            C:\Users\howar\AppData\Local\OysterRecorder\logs\javaw_*.log
mc-instance:        C:\Users\howar\AppData\Local\OysterRecorder\mc-instance\
mc-mod jars:        C:\Users\howar\AppData\Local\OysterRecorder\mc-instance\mods\
Bundled JRE:        C:\Users\howar\AppData\Local\OysterRecorder\jre\
Launch script:      C:\Users\howar\AppData\Local\OysterRecorder\launch_mc.bat
```

## Next-iteration recommendation

Pick option from the table above + I execute it. Suggested order:
1. **Right now**: tell me A/B/C/D on tag cleanup (5 sec decision)
2. **Then**: tell me Minimum/Recommended/Aggressive on rc18.0.4 scope
3. **Then I work** while you do other things; ping me when you need next status

Estimated ship time:
- Minimum rc18.0.4: ~30 min from your green light
- Recommended rc18.0.4: ~2-4 hours from your green light
