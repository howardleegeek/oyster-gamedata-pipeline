# PRD Compliance — MECE Checklist (2026-05-16)

> Goal: **互斥完备**（mutually exclusive + collectively exhaustive）PRD 要求清单。
> 每项必须能用**真实录像**的输出数据**单独**验证，不依赖另一项。
> Source: PRD-DATA-REQUIREMENTS.md + docs/PRD.md + lint_v3_prd_grounded.py.

---

## Group A — Files Present (5 件套, A1-A5)

| # | Requirement | Where in PRD | Verify on real session | Status |
|---|---|---|---|---|
| A1 | `recording.mp4` exists, > 0 bytes | §一.1️⃣ | `[ -f recording.mp4 ] && [ size > 0 ]` | ❓ |
| A2 | `action_camera.json` exists, valid JSON, non-empty array | §一.2️⃣ | `python -c 'import json; assert len(json.load(open("action_camera.json"))) > 0'` | ❓ |
| A3 | `gameinfo.xlsx` exists, single sheet, >= 14 rows | §一.3️⃣ | `openpyxl` load + sheet count == 1 + rows >= 14 | ❓ |
| A4 | `depth/` directory exists with ~1800 EXR files (1788–1810 tolerance) | §一.4️⃣ | `ls depth/*.exr \| wc -l → 1788–1810` | ❓ |
| A5 | `metadata.json` exists with game_exe/resolution/recordDpi | §六.22 | json parse + required keys present | ❓ |

---

## Group B — mp4 Properties (B1-B8)

Each verified via `ffprobe` on `recording.mp4`. Independent of action_camera.

| # | Requirement | Verify | Status |
|---|---|---|---|
| B1 | Resolution exactly 1920×1080 | `ffprobe -show_streams \| grep -E 'width=1920\|height=1080'` | ❓ |
| B2 | Frame rate exactly 30 fps (steady, not variable) | `ffprobe avg_frame_rate == 30/1 AND r_frame_rate == 30/1` | ❓ |
| B3 | Duration in [300, 360] seconds (5–6 min) | `ffprobe duration` | ❓ |
| B4 | Codec ∈ {h264, hevc/h265} | `ffprobe codec_name` | ❓ |
| B5 | Video bitrate within [6,12] Mbps; CRF ≤ 23 if CRF encoded | `ffprobe bit_rate / 1e6` | ❓ |
| B6 | Audio stream present, codec=aac | `ffprobe -show_streams stream=codec_type=audio` | ❓ |
| B7 | Audio continuous (no silence runs > 2s) | `ffmpeg silencedetect` parse | ❓ |
| B8 | No testsrc / synthetic pattern (real game footage) | extract frame N, AI-classify "game-content" — STATIC stop-gap until reliable | 🔴 hard |

---

## Group C — action_camera.json Field PRESENCE (C1-C20)

20 fields per PRD §5. Each is "this key exists in every frame entry, non-null".

| # | Field name (字面) | Type | Status |
|---|---|---|---|
| C1 | `frame` | int | ❓ |
| C2 | `time` | string `YYYY-MM-DD HH:MM:SS.fff` | ❓ |
| C3 | `fps` | float | ❓ |
| C4 | `route_type` | int | ❓ |
| C5 | `mouse_x` | float | ❓ |
| C6 | `mouse_y` | float | ❓ |
| C7 | `mouse_dx` | float | ❓ |
| C8 | `mouse_dy` | float | ❓ |
| C9 | `keyCode` | int (NOT list) | ❓ |
| C10 | `camera_position` | Vector3 | ❓ |
| C11 | `camera_rotation_oula` | Vector3 (字面 `oula` 不是 `euler`) | ❓ |
| C12 | `camera_rotation_quaternion` | Vector4 `[x,y,z,w]` | ❓ |
| C13 | `camera_Follow Offset` | Vector3 (字面 带空格 + 大写 F) | ❓ |
| C14 | `camera_intrinsics` | Object `{fx, fy, Cx, Cy}` (大写 Cx Cy) | ❓ |
| C15 | `camera_speed` | Vector3 (m/s) | ❓ |
| C16 | `player_position` | Vector3 | ❓ |
| C17 | `player_rotation_oula` | Vector3 | ❓ |
| C18 | `player_rotation_quaternion` | Vector4 | ❓ |
| C19 | `player_speed` | Vector3 (m/s) | ❓ |
| C20 | `metric_scale` | float | ❓ |

---

## Group D — action_camera VALUE CONSTRAINTS (D1-D10)

| # | Constraint | Verify on real session | Status |
|---|---|---|---|
| D1 | `frame` 连续 0…N-1 无跳号 | enumerate + diff | ❓ |
| D2 | N ≈ 9000 (5 min × 30 fps); tolerance [8900, 9100] | len(records) in range | ❓ |
| D3 | `fps` == 30.0 each frame | every frame value | ❓ |
| D4 | `route_type` ∈ {1, 2, 3} each frame | set membership | ❓ |
| D5 | `mouse_x`, `mouse_y` ∈ [0, 1] | range check | ❓ |
| D6 | All Vector3 / Vector4 are list of correct length | type+len check | ❓ |
| D7 | quaternion 模长 ∈ [0.99, 1.01] | sqrt(sum(c*c)) per frame | ❓ |
| D8 | `camera_intrinsics.fx == fy` (针孔) | == check | ❓ |
| D9 | `camera_speed`, `player_speed` magnitudes ≤ 100 m/s | sqrt(sum) <= 100 | ❓ |
| D10 | pitch ∈ [-90, 90]; yaw/roll ∈ [-180, 180] (角度范围) | range check on Euler | ❓ |

---

## Group E — Coordinate System (E1-E5)

| # | Constraint | Verify | Status |
|---|---|---|---|
| E1 | Left-handed coord system | walk N frames; compute X×Y dot Z; verify sign | ❓ |
| E2 | `camera_position` / `player_position` units = meters (1 block = 1 m) | absolute scale within reasonable range vs game | ❓ |
| E3 | yaw negated for MC→Buyer (per L1 oracle) | compare game_state yaw vs action_camera yaw — should be sign-flipped | ❓ |
| E4 | quaternion order `[x, y, z, w]` (NOT `[w, x, y, z]`) | static check: convention is xyzw | ❓ |
| E5 | velocity m/s (not blocks/tick); MC_TICKS_PER_SECOND × game_state velocity matches | compare game_state velocity × 20 vs action_camera speed | ❓ |

---

## Group F — gameinfo.xlsx Fields (F1-F14)

| # | Field name | Type | Verify | Status |
|---|---|---|---|---|
| F1 | `game_name` | string | exists + non-empty | ❓ |
| F2 | `game_version` | string | matches MC version | ❓ |
| F3 | `platform` | string | "Java Edition" | ❓ |
| F4 | `scene_name` | string | matches MC dimension | ❓ |
| F5 | `weather` | string | from mc-mod IPC | ❓ |
| F6 | `time_of_day` | string | from mc-mod IPC | ❓ |
| F7 | `character_name` | string | operator-set | ❓ |
| F8 | `character_class` | string | operator-set | ❓ |
| F9 | `operator_id` | string | operator-set | 🔴 known missing |
| F10 | `recording_date` | string `YYYY-MM-DD` | filled by recorder | ❓ |
| F11 | `total_frames` | int | matches action_camera count | ❓ |
| F12 | `video_duration_sec` | float | matches mp4 duration | ❓ |
| F13 | `route_type` | int | matches action_camera majority | ❓ |
| F14 | `notes` | string | operator-set | 🔴 known missing |

---

## Group G — gameinfo.xlsx EXTRA rc19.0.3 rows (G1-G5)

| # | Row key | Verify | Status |
|---|---|---|---|
| G1 | `world_gravity_mps2` == 32.0 | xlsx cell read | 🟢 (rc19.0.3 emits) |
| G2 | `coord_system` == "left_handed_X_right_Y_up_Z_forward" | xlsx cell read | 🟢 |
| G3 | `velocity_unit` == "m/s" | xlsx cell read | 🟢 |
| G4 | `mc_blocks_to_meters` == 1.0 | xlsx cell read | 🟢 |
| G5 | `mc_ticks_per_second` == 20.0 | xlsx cell read | 🟢 |

---

## Group H — depth/*.exr (H1-H6)

| # | Constraint | Verify | Status |
|---|---|---|---|
| H1 | Files named `000000.exr` to `001799.exr` | filename pattern check | ❓ |
| H2 | Count exactly 1800 (tolerance 1795–1805) | `ls \| wc` | ❓ |
| H3 | Each file 1920×1080 single-channel `Z` | OpenEXR header read | ❓ |
| H4 | Pixel type float32 | OpenEXR header dtype | ❓ |
| H5 | Linear depth in meters (≤ 192m far plane); invalid pixels = 0 | min/max value check + 0 = sky mask | ❓ |
| H6 | Sampling rate 6 fps (file_n.exr should correspond to frame N × 5 in mp4) | timestamp alignment | ❓ |

---

## Group I — Route Diversity (I1-I3)

Per PRD §三. Cross-session not single-session.

| # | Constraint | Verify (multiple sessions) | Status |
|---|---|---|---|
| I1 | 50% route_type=1, 50% split among 2 & 3 (per BATCH) | batch aggregate | 🔴 not enforced in single recording |
| I2 | WASD balance: W ≈ 40%, A/S/D each ≈ 20% (per session) | parse inputs.jsonl | ❓ lint #18 checks |
| I3 | route_type=3 cyclic (前 10s == 最后 10s 同场景) | scene fingerprint compare | 🔴 known not implemented |

---

## Group J — Quality (J1-J7)

Operator behavior + screen content.

| # | Constraint | Verify | Status |
|---|---|---|---|
| J1 | No UI popup / inventory / settings on screen | frame OCR / classifier (manual review) | ❓ |
| J2 | No first↔third person switch during session | constant intrinsics check | ❓ lint #12 |
| J3 | No death/respawn (frame continuity OK from D1) | covered by D1 | ❓ |
| J4 | No long stationary (≥1min no input) | inputs.jsonl temporal gap | ❓ lint #19 |
| J5 | Fullscreen MC (window covers 1920×1080) | metadata.json check | ❓ |
| J6 | No macro / robotic input patterns | inputs entropy check | ❓ lint covers |
| J7 | Native 30fps (no 60→30 downsample) | metadata.json source_fps | ❓ |

---

## Total: 89 items (A:5 + B:8 + C:20 + D:10 + E:5 + F:14 + G:5 + H:6 + I:3 + J:7 = 83)

Wait — let me recount: 5+8+20+10+5+14+5+6+3+7 = **83 items**.

The memory's "56 P0" was a subset. "93 reqs" (memory) ≈ 83 here + ~10 P1 items I didn't break out separately.

---

## Verification Strategy

1. **Static-only items** (~30): code inspection, schema check, file presence — verify WITHOUT recording
2. **Real-recording items** (~50): need an actual session — verify ONLY with real data
3. **Cross-batch items** (~3 in I): need multiple sessions

**The right RFC sequence**:
- **RFC-1** (this turn): build `bin/prd_compliance_audit.py` that runs this MECE checklist against a session_dir and reports ✅/❌ per A1..J7. **No recording needed to build this tool.**
- **RFC-2** (after Howard records 1 min): run the audit on the real session, see initial pass count, identify gaps
- **RFC-3..N**: each remaining gap = one cluster RFC

**Status legend in this doc**: 🟢 verified-passing / 🟡 implemented-not-yet-verified / ❓ unverified / 🔴 known-broken
