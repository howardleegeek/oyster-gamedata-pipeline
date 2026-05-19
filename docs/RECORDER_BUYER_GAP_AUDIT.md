# Recorder vs Buyer-Spec Gap Audit (v0.18.0)

> Howard 2026-05-05: "帮我看看还有什么隐患"
>
> Red Team audit of `bin/recorder_consumer_lite.py` v0.18.0 output
> against the Lark PDF buyer spec. lint v3 (G165) is forgiving in
> several places — this doc lists everything a careful buyer ML team
> would flag even when lint shows 24/24 PASS.

---

## Severity legend

- 🔴 **Critical** — buyer will likely reject the clip
- 🟠 **Major** — buyer will accept with workaround / re-grading
- 🟡 **Minor** — buyer might not even notice; cleanup later
- ✅ **Fixed in v0.18.0** — no longer a gap

---

## A. Data quality gaps

| # | Gap | Severity | Where | Fix path |
|---|-----|----------|-------|----------|
| A1 | No 1800 `.exr` depth files at 6 fps | 🔴 | `depth/` | DA-V2 monocular (G261/G266 cluster) or Replay Mod GPU depth (per `RESEARCH_DEPTH_CAPTURE_MC.md`) |
| A2 | `cameraX/Y/Z` always 0.0 — no real 6DoF position | 🔴 | `action_camera.json` | Needs game-engine telemetry hook (Replay Mod, JNI to MC, or memory reading) — out of scope for stop-gap |
| A3 | `quaternion: [0,0,0,1]` identity placeholder, not real camera rotation | 🔴 | `action_camera.json` | Same as A2 — needs engine telemetry |
| A4 | `intrinsics.yaml` uses default 70° FOV; tester may have changed in-game | 🟠 | `intrinsics.yaml` | Read FOV from MC's `options.txt` |
| A5 | `action_camera` record count tracks raw input events, not 9000 frame-aligned records | 🟠 | `action_camera.json` | Resample to 30 Hz frame-aligned records; interpolate gaps |
| A6 | Mouse `mouseX/Y` are absolute screen coords, not in-game look vector | 🟠 | `action_camera.json` | Convert to delta-yaw/pitch in radians |
| ✅ | Quaternion array format `[x,y,z,w]` (was scalars only) | — | — | Done v0.18.0 |
| ✅ | `intrinsics.yaml` exists at all (was missing) | — | — | Done v0.18.0 |
| ✅ | `actual_duration_sec` + `partial` flag in systeminfo when <5 min | — | — | Done v0.18.0 |

## B. Audio gaps

| # | Gap | Severity | Where | Fix path |
|---|-----|----------|-------|----------|
| B1 | No `audio_events.json` (gunshots / footsteps / dialog timestamps) | 🟠 | clip dir | Wrap `bin/audio_track_extractor.py` — G266 cluster |
| B2 | Audio is embedded in `video.mp4`, not exposed as separate `audio.flac` | 🟡 | clip dir | Demux post-record; G266 |
| B3 | If tester's mic is muted / no audio device, clip has zero audio | 🟠 | recording phase | Currently silently degrades; should warn tester before record |
| B4 | Audio captures tester's mic, may include real-world voice / ambient noise (privacy) | 🟠 | privacy | Capture system loopback only, not microphone |

## C. Metadata / provenance gaps

| # | Gap | Severity | Where | Fix path |
|---|-----|----------|-------|----------|
| C1 | No `metadata.json` with `timestamp / location / device_id / session_id` (lint criterion 22) | 🟠 | clip dir | Generate from recorder context |
| C2 | `operator_id` hardcoded `"lite-recorder"` — not traceable to specific tester | 🟠 | `gameinfo.xlsx` | Read from a config file or first-run prompt |
| C3 | `character_name` hardcoded `"DataPilot"` — buyer wants real MC username | 🟠 | `gameinfo.xlsx` | Read from MC `launcher_profiles.json` |
| C4 | No `MANIFEST.json` with sha256 per file — tampering not detected | 🟠 | clip dir | G262 cluster |
| C5 | Timestamps in local time; buyer-spec wants UTC | 🟡 | all | Use `datetime.now(timezone.utc)` |
| C6 | Recording session_id not unique cross-machine | 🟡 | metadata | UUID4 per recording |

## D. Robustness / failure modes

| # | Gap | Severity | Where | Fix path |
|---|-----|----------|-------|----------|
| D1 | Disk full mid-record → ffmpeg crashes, partial mp4 may be unplayable | 🟠 | recording | Pre-flight free-space check (≥500 MB) |
| D2 | MC crashes mid-record → ffmpeg sees no MC process → finalize triggers, BUT video.mp4 may be corrupt | 🟠 | recording | mp4 fastfinal (movflags=+faststart), atomic mv |
| D3 | Tester closes recorder window mid-record → `_on_close` fires `_stop_ffmpeg` (q to stdin) → may not flush container properly | 🟡 | shutdown | Already handled, add `proc.wait` before destroy |
| D4 | gdigrab fails on MC exclusive fullscreen — silent 0-byte mp4 | 🟠 | recording | Detect fullscreen via Win32, switch to dxgi-grab or warn tester to use windowed |
| D5 | Multi-monitor — gdigrab desktop captures primary only | 🟡 | recording | Acceptable; buyer expects single-monitor anyway |
| D6 | High-DPI Windows scaling 150% → window rect coords are scaled, intrinsics computation off | 🟡 | intrinsics | Read GetDpiForWindow ratio (we already do this) and compensate |

## E. Tester UX gaps that affect data collection

| # | Gap | Severity | Where | Fix path |
|---|-----|----------|-------|----------|
| E1 | Tester arms recording but MC not running → recorder waits forever silently | ✅ | — | Fixed v0.16.0 (live status + arm-vs-MC display) |
| E2 | Black console flashes every 2s during MC-detect poll | ✅ | — | Fixed v0.15.0 (CREATE_NO_WINDOW) |
| E3 | Tester clicks ▶ → window iconifies before status messages render | ✅ | — | Fixed v0.17.0 (deferred iconify) |
| E4 | Tester double-clicks wrong .exe (old v0.4 lying around) | 🟡 | distribution | Documented; we now ship desktop shortcut |
| E5 | Tester closes window mid-record by accident — recording lost | 🟠 | UI | Confirmation dialog before close-during-record |
| E6 | No way for tester to abort recording without losing the partial clip | ✅ | — | Fixed v0.9.0 (disarm preserves) |

## F. Integrity / pipeline gaps

| # | Gap | Severity | Where | Fix path |
|---|-----|----------|-------|----------|
| F1 | No clip integrity verification before backend upload | 🟠 | upload | G268 uploader does sha256 verify |
| F2 | No retry on partial upload | 🟡 | upload | Resumable multipart upload (G190 backend already supports) |
| F3 | Recorder version `lite-vX.Y.Z` not signed — could be spoofed | 🟡 | provenance | Code-signing cert (Howard dropped, accept risk) |
| F4 | `~/OysterRecorder.log` keeps growing — eventually fills disk | 🟡 | logs | Rotate at 10 MB |

## G. Privacy / consent gaps

| # | Gap | Severity | Where | Fix path |
|---|-----|----------|-------|----------|
| G1 | gdigrab captures whole screen — may include browser, Discord, notifications, personal info | 🟠 | recording | Window-only mode (v0.14.0 has guard but falls back to desktop on title issues) |
| G2 | No EULA / consent flow — tester records before agreeing to terms | 🟠 | first-run | First-run consent dialog |
| G3 | Mic captures real-world ambient | duplicate B4 | — | — |

---

## Buyer reject scenarios (worst-case)

If buyer ML team runs strict validation beyond lint v3:

1. **Open clip → no `depth/*.exr` files** → reject "missing depth"
2. **Parse action_camera.json → cameraX/Y/Z all zero** → reject "no 6DoF"
3. **Compare quaternion across frames → constant identity** → reject "no rotation"
4. **Audio analysis → no audio events** → soft-reject (acceptable but downgraded)
5. **Check duration → <5 min** → reject "partial recording"

---

## Recommended next 3 releases

| Release | Closes gaps | Effort |
|---|---|---|
| **v0.19.0** | A1 (depth EXR via DA-V2 lazy-download) | High — 1 day |
| **v0.20.0** | A4/C2/C3 (read MC options.txt + launcher_profiles.json) | Low — 2h |
| **v0.21.0** | C1/C4 (metadata.json + MANIFEST.json) | Low — 2h |

After those: ~15 of 24 buyer concerns closed; remaining are A2/A3 (real 6DoF, requires engine hook) — **only Replay Mod or full Rust app can solve** per `docs/RESEARCH_DEPTH_CAPTURE_MC.md`.
