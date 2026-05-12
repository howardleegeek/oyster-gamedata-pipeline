# PRD Compliance Checklist — rc17.2.3

**Last updated**: 2026-05-12
**Tag**: `recorder-v0.28.0-rc17.2.3`
**Installer**: `GameDataRecorder-Setup-recorder-v0.28.0-rc17.2.3.exe` (859 MB)

Use this checklist after each test session to verify deliverables.

---

## Session output files (per recording)

Path: `%LOCALAPPDATA%\GameData Recorder\recordings\session_<TS>_<HASH>\`

| File | Required | rc17.2.3 status | How to verify |
|---|---|---|---|
| `recording.mp4` | ✅ PRD | ✅ | size ≥ 200 MB for 5-min session; play in any player |
| `metadata.json` | ✅ PRD | ✅ | open, check `game_exe`/`game_resolution`/`recorder_version` |
| `metadata.json` → `recordDpi` | ✅ PRD page 3 | ✅ (BD) | open, look for `"recordDpi": 1.0/1.5/2.0` field |
| `action_camera.json` | ✅ PRD | ✅ | open, check 300+ frames as JSON array |
| `frames.jsonl` | ✅ PRD | ✅ | one line per frame with `idx` + `t_ns` |
| `fps_log.json` | internal | ✅ | per-second FPS log |
| `inputs.jsonl` | ✅ PRD | ⚠️ markers only (BG not yet shipped) | should have hundreds of events; currently only ~5 markers |
| `gameinfo.xlsx` | ✅ PRD page 5 | ✅ (BJ) | open in Excel; check Session / GameEvents / BlockStats / BiomeVisits sheets |
| `depth/depth_*.exr` | ✅ PRD page 6 | ✅ (BJ, async background) | ~300 files at 1 Hz, 1920×1080 32-bit float depth |
| `lint_result.json` | new (BN) | ✅ | open, check `overall_status: PASS` or `FAIL` |

---

## `action_camera.json` per-frame fields (305 frames typical)

For EACH frame entry, verify:

| Field | rc17.0.4 (before) | rc17.2.3 (now) | Source |
|---|---|---|---|
| `frame_index` | ✅ | ✅ | recorder |
| `timestamp` / `timestamp_ns` | ✅ | ✅ | recorder |
| `input_modality` | ✅ "keyboard_mouse" | ✅ | recorder |
| `mouseX` / `mouseY` | ⚠️ 0.5 stuck | ⚠️ 0.5 stuck (BG not yet) | input_recorder |
| `mouse_dx` / `mouse_dy` | ⚠️ 0 stuck | ⚠️ 0 stuck (BG not yet) | input_recorder |
| `keyCode` | ❌ [] empty | ❌ [] (BG not yet) | input_recorder |
| **`camera_position`** | ❌ null | ✅ `{x, y, z}` non-null | mc-mod via sibling-dir IPC (BH-narrow) |
| **`rotation_oula`** | ❌ null | ✅ Euler | mc-mod via sibling-dir |
| **`rotation_quaternion`** | ❌ null | ✅ | mc-mod |
| **`camera_rotation_quaternion`** (xyzw) | ❌ null + wrong order | ✅ `[x, y, z, w]` unit quat | BD schema + BH-narrow data |
| **`player_position`** | ❌ null | ✅ `{x, y, z}` | mc-mod |
| **`player_rotation_quaternion`** (xyzw) | ❌ null | ✅ `[x, y, z, w]` | BD + BH-narrow |
| `Follow_Offset` | ❌ null | ❌ null (合理 — 第一人称 MC) | n/a |
| `camera_intrinsics` (was `intrinsics`) | ❌ wrong key + null | ✅ `{fx, fy, cx, cy}` | BD schema rename + writer |
| `speed` / `player_speed` | ⚠️ 0 stuck | ⚠️ 0 stuck (no velocity in IPC yet) | mc-mod |
| `route_type` (∈ {1,2,3}) | ❌ null | ✅ `1` (DEFAULT) | BD |
| `metric_scale` | ✅ 1.0 | ✅ 1.0 | recorder |

---

## `metadata.json` `input_stats` block

| Field | rc17.2.3 |
|---|---|
| `total_keyboard_events` | ⚠️ 0 (BG not yet) |
| `wasd_apm` | ⚠️ 0.0 |
| `unique_keys` | ⚠️ 0 |
| `button_diversity` | ⚠️ 0.0 |
| `mouse_movement_std` / `mouse_max_*` | ⚠️ 0.0 |
| `recordDpi` (at top level) | ✅ 1.0 / 1.5 / 2.0 (BD) |
| `hardware_specs.cpu/gpu/system` | ✅ |
| `start_timestamp` / `end_timestamp` / `duration` | ✅ |

---

## Post-session self-validation (BN)

Open `lint_result.json` from session dir:

```json
{
  "lint_version": "v3",
  "ran_at": "2026-05-12T...",
  "session_dir": "...",
  "total_criteria": 32,
  "passed": <N>,
  "failed": <M>,
  "failures": [...],
  "overall_status": "PASS" | "FAIL"
}
```

**Expected rc17.2.3**: `passed: 27-28`, `failed: 4-5` — all 4-5 failures should relate to `inputs.jsonl` / `input_stats` (BG not yet shipped) or `speed` (no velocity IPC yet).

If `failed > 5` OR failures NOT input-related → **report back, something else broke**.

---

## End-to-end smoke test procedure (5 min)

1. Install `GameDataRecorder-Setup-recorder-v0.28.0-rc17.2.3.exe` (859 MB)
2. Verify desktop has 2 icons: `GameData Recorder` + `Open Recordings Folder`
3. Double-click `GameData Recorder` (**only ONCE**)
4. Launch MC (auto-spawned)
5. Play 3-5 minutes
6. **Esc → Save and Quit** (NOT Alt+F4, NOT task-manager kill)
7. Wait 1-2 seconds, watch bottom-right of screen:
   - **Green toast** = lint v3 PASS → SUCCESS
   - **Red toast** = lint v3 FAIL with N failed criteria + Explorer auto-opens session dir
8. Open `Open Recordings Folder` shortcut → latest session subfolder
9. Verify file list matches the per-session table above
10. Open `action_camera.json`, jump to frame 100, verify `camera_position` IS NON-NULL

---

## Known gaps deferred to rc17.3 / rc17.4

| Gap | Plan |
|---|---|
| `inputs.jsonl` per-event keystrokes + mouse | Stream BG-rescue → rc17.3 |
| `mouseX/Y` per-frame real cursor | Stream BG-rescue → rc17.3 |
| `mouse_dx/dy` per-frame delta | Stream BG-rescue → rc17.3 |
| `speed` / `player_speed` real values | Need mc-mod velocity IPC → rc17.4 |
| OTLP server telemetry | Stream OTLP → rc17.3 or rc17.4 |
| `Recording::stop()` idempotency | Audit B2 → rc17.3 |
| `session_id` 8→16 hex (collision-safe) | Audit G4 → rc17.3 |

---

## Customer hand-off readiness

After Howard runs the smoke test:

- [ ] mp4 plays back correctly (visual sanity)
- [ ] All 10 expected files present in session dir
- [ ] `camera_position` non-null in action_camera.json
- [ ] `lint_result.json` shows `overall_status: PASS` (or FAILs are only input-related)
- [ ] gameinfo.xlsx opens in Excel without error
- [ ] At least 1 depth EXR file in session_dir/depth/

If ALL 6 boxes checked → **session is shippable to tester**.
If any unchecked → fix that specific failure before sending.
