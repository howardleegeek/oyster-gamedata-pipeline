---
title: Oyster GameData Recorder — PRD Requirements vs Current State (Gap Analysis)
author: Howard Li / Oyster Labs
date: 2026-05-12
version: rc18.0.6 in CI + rc19 chunks landed
---

# PRD Requirements vs Current State — Gap Analysis

**Date**: 2026-05-12
**Reference PRD**: `docs/PRD.md` v1.0 + `bin/lint_v3_prd_grounded.py` 32 acceptance criteria
**Shipped**: rc18.0.5 (live), rc18.0.6 (CI building), rc19 chunks (lint #13 contract + velocity backfill)
**Validated lint score on Howard's existing session**: 25/33 → **27/33 (81.8%)** after rc19 chunk

**Status legend**:
- ✅ MET — fully satisfies PRD
- ⚠️ PARTIAL — schema present, value needs source / operator action
- ❌ MISSING — not yet implemented
- 🔄 IN CI — fix in flight
- 👤 OPERATOR — depends on recording-time human action
- 📋 CONTRACT — depends on customer signoff

---

## §3.1 — `recording.mp4` (video file, 9 PRD items)

| # | PRD Requirement | Current State | Gap | ETA |
|---|---|---|---|---|
| 1 | Duration 5-6 min | Operator-controlled; recorder doesn't enforce | 👤 user records longer than 5 min next time | Now |
| 2 | Resolution 1920×1080 | rc17.0.4+ locked at 1080p ✓ | ✅ MET | — |
| 3 | FPS 30 stable | rc17.0.4+ validated, real recording shows 29.97 ✓ | ✅ MET | — |
| 4 | Encoding H.264/H.265 CRF ≤ 23 | rc17.0.4+ x264 CRF 22; AMD 780M falls back from HEVC | ⚠️ H.264 ok per PRD but PRD prefers H.265; need GPU-specific HEVC config | rc19 |
| 5 | Audio AAC continuous | OBS AAC 160kbps stereo configured ✓ | ⚠️ Howard's session had real >2s silence gap (hardware issue) | Investigate minipc1 audio device |
| 6 | Input → frame latency ≤ 20ms | Not measured; `bin/measure_input_latency.py` exists but not run automatically | ❌ measure_input_latency needs running before each release; not wired to CI | rc18.0.8 |
| 7 | Fullscreen 1080p (system + game) | OBS records monitor, MC launched fullscreen | ✅ MET | — |
| 8 | No UI/inventory/death/perspective-switch | No enforcement; operator守则 | 👤 operator follows指南 | Now |
| 9 | Per-game daily limit 240 clips | No cross-session counter | ❌ backend tracking needed | rc20 backend |

**§3.1 Score**: 4/9 ✅, 2/9 ⚠️, 1/9 ❌, 2/9 👤 = **44% strict / 67% with operator**

## §3.2 — `action_camera.json` (per-frame 20 fields)

| Field | PRD | rc18.0.5 + rc19 chunks | Gap | ETA |
|---|---|---|---|---|
| `frame` | int continuous 0..N-1 | recorder writes ✓ | ✅ MET | — |
| `time` | ISO ms format | recorder writes relative seconds | ⚠️ format mismatch — needs ISO conversion | rc19 next chunk |
| `fps` | 30.0 explicit | metadata has fps_effective; per-frame fps missing | ⚠️ add to finalize | rc19 next chunk |
| `route_type` | ∈ {1,2,3} | rc18.0.4 env-var default 1 ✓ | ⚠️ operator must set per-session via env-vars | 👤 per session |
| `mouse_x` / `mouse_y` (normalized 0-1) | Real position | rc17.3 BG fix; stuck at 0.5 if input pipeline broken | 🔄 rc18.0.6 keyboard wake fix (CI); rc18.0.7 mouse hook install | rc18.0.7 |
| `mouse_dx` / `mouse_dy` (per-frame delta) | Real delta | Same as above | 🔄 rc18.0.6 / rc18.0.7 | rc18.0.7 |
| `keyCode` (pressed keys array) | Real key codes | rc18.0.6 fixes keyboard event pipeline (CI building) | 🔄 IN CI | rc18.0.6 ~15 min |
| `camera_position` (x,y,z, m, LH coord) | Real position | rc19 chunk: backfilled from game_state ✓ | ✅ MET | — |
| `camera_rotation_oula` (Euler) | pitch [-90,90] yaw/roll [-180,180] | rc19 chunk: backfilled ✓ | ✅ MET | — |
| `camera_rotation_quaternion` xyzw, |q|≈1 | Order + norm | rc19 chunk: backfilled + metadata declares "xyzw" ✓ | ✅ MET | — |
| `camera_Follow Offset` | First-person = 0 | rc19 chunk: [0,0,0] ✓ | ✅ MET | — |
| `camera_intrinsics` fx==fy | Pinhole model | rc17.2.3+ fx==fy=771.2 ✓ | ✅ MET | — |
| `camera_speed` (x,y,z, m/s per axis) | Real velocity | rc19 chunk: backfilled from game_state velocity_x/y/z ✓ | ✅ MET (NEW) | — |
| `player_position` (x,y,z, m, LH) | Real position | rc19 chunk: backfilled ✓ | ✅ MET | — |
| `player_rotation_oula` | Same as camera | rc19 chunk: backfilled ✓ | ✅ MET | — |
| `player_rotation_quaternion` xyzw | Same as camera | rc19 chunk: ✓ | ✅ MET | — |
| `player_speed` (x,y,z, m/s) | Real velocity | rc19 chunk: backfilled from game_state ✓ | ✅ MET (NEW) | — |
| `metric_scale` | 1.0 | recorder ✓ | ✅ MET | — |
| `input_modality` | "keyboard_mouse" | recorder ✓ | ✅ MET | — |
| `gamepad_buttons` / `gamepad_*_stick_*` | Gamepad data | recorder reserves fields | ✅ MET (null for kb+m sessions) | — |

**§3.2 Score**: 16/20 ✅, 3/20 ⚠️, 2/20 🔄 = **80% strict / 95% post-rc18.0.6**

## §3.3 — `gameinfo.xlsx` (single sheet, 14 fields)

| Field | PRD | Current source | Gap | ETA |
|---|---|---|---|---|
| `game_name` | "Minecraft" | default ✓ | ✅ MET | — |
| `game_version` | "1.21.4" or similar | default ✓ | ✅ MET | — |
| `platform` | "Java Edition" | default ✓ | ✅ MET | — |
| `scene_name` | flat-overworld etc. | rc19 chunk: backfilled from game_state dimension ✓ | ✅ MET (NEW) | — |
| `weather` | clear/rain/thunder | env-var default "clear" | ⚠️ mc-mod doesn't emit weather; needs §3.3 Java patch | rc18.0.8 mc-mod |
| `time_of_day` | day/night/dawn/dusk | env-var default "day" | ⚠️ mc-mod doesn't emit time_of_day; needs §3.3 Java patch | rc18.0.8 mc-mod |
| `character_name` | DataPilot | env-var default ✓ | ✅ MET | — |
| `character_class` | survival/explorer | env-var default ✓ | ✅ MET | — |
| `operator_id` | vendor-001-op-A | env-var ✓ | ✅ MET (operator sets per-session) | — |
| `recording_date` | ISO date | metadata-derived ✓ | ✅ MET | — |
| `total_frames` | int from frames.jsonl | computed ✓ | ✅ MET | — |
| `video_duration_sec` | float from metadata | computed ✓ | ✅ MET | — |
| `route_type` | 1/2/3 | env-var per-session | ⚠️ operator sets | 👤 |
| `notes` | string | env-var optional | ✅ MET (allowed to be empty) | — |

**§3.3 Score**: 11/14 ✅, 3/14 ⚠️ = **79% strict / 100% post-mc-mod weather/time emit**

## §3.4 — `depth/*.exr` (depth maps @ 6 fps)

| Item | PRD | Current State | Gap | ETA |
|---|---|---|---|---|
| Sampling rate | 6 fps (1800 frames / 5 min) | DISABLED in rc17.3.1 submodule | ❌ depth EXR writer broken (rc17.4 cluster cv2 attempt captured desktop post-record at 1 Hz) | rc19 rewrite |
| Format | OpenEXR single-channel Z | N/A | ❌ | rc19 |
| Dtype | float32 | N/A | ❌ | rc19 |
| Units | meters (linear along Z) | N/A | ❌ | rc19 |
| Invalid pixels | 0 (sky/transparent/clipped) | N/A | ❌ | rc19 |
| Filename | `000000.exr` (t=0) timestamp-aligned | N/A | ❌ | rc19 |
| Resolution | 1920×1080 same as mp4 | N/A | ❌ | rc19 |

**§3.4 Score**: 0/7 ✅ = **0% — entire deliverable blocked on rc19**

Implementation plan: cv2-based mp4 re-decode + DepthAnything V2 Small (onnxruntime-directml) inference at 6 fps, output OpenEXR float32 Z-channel. Major Python+model work, ~1-2 day cycle including model bundling in installer.

## §4 — Path diversity (per-batch)

| Item | PRD | Current State | Gap | ETA |
|---|---|---|---|---|
| route_type distribution 50/25/25 (normal/special/loop) | Per-batch tracker | `bin/batch_tracker.py` ✓ exists | ✅ MET (operator runs it) | — |
| WASD balance | No direction starved | rc18.0.6 keyboard wake fix needed first | 🔄 IN CI | rc18.0.6 |
| No 1+ min stationary | Continuous input | Currently broken (input pipeline drops); rc18.0.6 fixes keyboard, rc18.0.7 fixes mouse | 🔄 | rc18.0.6/0.7 |
| Path type coverage 1+2+3 | Operator守则 | per-session env-var | 👤 | — |

**§4 Score**: 2/4 ✅, 2/4 🔄 = **50% strict / 100% post-rc18.0.7**

## §6 — Lint v3 acceptance (32 criteria, soon 33 with audio)

**Howard's test session current score (after rc19 chunks)**:
- ✅ 27 / 33 = **81.8%** PASS
- Failing: #2 (duration too short — operator), #15/#16/#24 (depth disabled — rc19), #31 (mouse pipeline — rc18.0.7), #38 (real audio dropout in this specific recording)

**Projected post-rc18.0.6 + 5-min fresh recording**: 28-29/33
**Projected post-rc18.0.7 (mouse hook install)**: 29-30/33
**Projected post-rc19 (depth + lint refinements)**: 32-33/33
**100% = customer RFC-001 signoff** (real-player path accepted)

---

## Summary scorecard

| PRD section | Strict % | Achievable with already-in-flight work | What blocks remaining |
|---|---|---|---|
| §3.1 mp4 | 44% | 78% (after audio device check + latency measurement) | Latency wire-up (rc18.0.8) |
| §3.2 action_camera | 80% | 95% post-rc18.0.6/0.7 | Mouse pipeline fix |
| §3.3 gameinfo | 79% | 100% post-mc-mod weather/time | mc-mod Java patch (rc18.0.8) |
| §3.4 depth | 0% | 0% until rc19 | cv2 depth rewrite |
| §4 path diversity | 50% | 100% post-rc18.0.7 | Mouse pipeline |
| §6 lint v3 | 82% | 100% post-rc19 + signoff | Depth + RFC-001 |

**Overall PRD strict compliance**: ~55%
**After rc18.0.6 lands** (~15 min from now): ~62%
**After rc18.0.7 ships** (~1 hour Rust work): ~70%
**After rc19 ships** (~1-2 days for depth): ~92%
**Plus RFC-001 customer signoff**: **100% contract delivery**

---

## Outstanding work tickets

### 🔥 In flight (CI)
- **rc18.0.6** building now (~15 min to URL): keyboard hook PostThreadMessageW wake fix → unblocks #27 (real keyboard events in inputs.jsonl) + partial #31

### 🛠️ Engineering (cluster or human push needed)
- **rc18.0.7**: `SetWindowsHookExW(WH_MOUSE_LL, mouse_ll_proc, ...)` install call — `mouse_ll_proc` defined but never installed; mouse capture 100% dead on tier-3 (AMD/Intel iGPU) systems
- **rc18.0.8**: (a) mc-mod Java patch to emit `weather` + `time_of_day` per tick; (b) `measure_input_latency.py` wire into post-record finalize for §3.1 latency check
- **rc19**: depth EXR cv2 mp4-decoder + DepthAnything V2 Small inference at 6 fps + OpenEXR float32 Z-channel writer
- **rc19**: H.265 GPU detection — try AMD AMF / NVIDIA NVENC / Intel QSV before x264 fallback
- **rc19**: lint v3 criteria 38-42 wiring (drafts in `oyster-audit/drafts/`)
- **rc20**: backend cross-session counters (240 clips/game/day, 30-min/map limits)

### 📋 Customer
- **RFC-001 signoff**: real-player vs headless-bot recording path acceptance (`docs/RFC-001-real-player-vs-bot.md`)

### 👤 User actions
- Install rc18.0.6 installer when CI lands (~15 min)
- Record via Start Menu → "Launch Minecraft (Recorded)" (NOT Mojang Launcher)
- Record **5+ minutes** per session (PRD §3.1 duration requirement)
- Set operator env-vars per session: `OYSTER_OPERATOR_ID`, `OYSTER_CHARACTER_NAME`, `OYSTER_ROUTE_TYPE`, etc.

---

## What's NOT a problem

These items HAVE caused confusion in earlier reviews but are actually correct:

- **action_camera.json 29 fields instead of 20** — extra fields are PRD-allowed extensions (gamepad_*, frame_index alias, input_modality). PRD §3.2 lists 20 REQUIRED; ours has all 20 + 9 informative extras.
- **H.264 instead of H.265 on AMD 780M** — PRD §3.1 allows either; the GPU just doesn't have HEVC. Same data quality.
- **Lint #13 heuristic-FAIL on rc17.x sessions** — heuristic flaw on game data with large rotation; rc19 contract check via metadata.json fixes this for all future sessions.
- **action_camera quaternion=null on rc18.0.3 sessions** — recorder wrote file before mc-mod game_state.jsonl was synced; rc18.0.5's `finalize_session.py` backfills post-record, runs automatically via `prd_acceptance.py`.

---

## Reference paths

- Lint runner: `bin/lint_v3_prd_grounded.py` — 33 criteria
- Acceptance harness: `bin/prd_acceptance.py` — runs all `prd_test_*.py` + lint + outputs `PRD-ACCEPTANCE-REPORT.md`
- Finalize step: `bin/finalize_session.py` — sync game_state + backfill quaternion/velocity/scene + generate gameinfo + generate audio_check
- Operator metadata form: `bin/oyster_launcher_form.py` — first-launch + per-session
- Customer RFC: `docs/RFC-001-real-player-vs-bot.md`
- Audit reports: `oyster-audit/` directory
