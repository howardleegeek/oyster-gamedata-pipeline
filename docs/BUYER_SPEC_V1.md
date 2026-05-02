# Buyer Spec v1 — Authoritative Reference

> **Source**: Howard's project background doc, paste 2026-05-02. This is the
> canonical buyer-spec v1 contract. Any pipeline change MUST be verified
> against this document.

## Project background

Train interactive world models from human gameplay traces. The data must be:
- **Clean, coherent** environment-exploration trajectories
- **Character + camera** trajectories (not UI clicks / menu interaction)
- Goal: model how the world responds to player actions

## 4 deliverables per recording

| # | File | Format | Purpose |
|---|---|---|---|
| 1 | `video.mp4` | mp4 | 5–6 min, 1920×1080, 30 fps |
| 2 | `action_camera.json` | json | Per-frame 20-field telemetry |
| 3 | `gameinfo.xlsx` | excel | Operator-curated metadata |
| 4 | `depth/*.exr` | OpenEXR (single-channel) | View-space metric depth, 6 fps |

## Hard requirements

- **Duration**: 5 ≤ x ≤ 6 minutes
- **Same scene**: ≤ 30 min total per scene
- **Per person per game**: ≤ 240 clips (≈ 20 hours)
- **Resolution**: 1920×1080 system-wide AND game window
- **FPS**: 30 fps stable, both video AND game (no dynamic FPS)
- **Latency**: ≤ 20 ms (action-to-frame)
- **Sound**: present, continuous, no foreign noise, no NPC dialogue spam

## Coordinate system (left-hand)

**Character:**
- right → +X
- up → +Y
- front → +Z

**Camera (Euler):**
- Pitch (rotate around X): nose-up +, nose-down -, range [-180, 180]
- Yaw (rotate around Y): turn right +, turn left -, range [-180, 180]
- Roll (rotate around Z): tilt right +, tilt left -, range [-180, 180]

**Units**: m / s for velocity, m for world position.

## action_camera.json — 20 fields per frame

| Field | Type | Notes |
|---|---|---|
| `frame` | int | 0-indexed frame number |
| `time` | string | `YYYY-MM-DD HH:mm:ss.SSS` |
| `fps` | float | Live frame rate |
| `route_type` | int | 1=normal / 2=special / 3=loop |
| `mouse_x`, `mouse_y` | float | Normalized [0, 1] |
| `mouse_dx`, `mouse_dy` | float | Normalized [-1, 1] |
| `keyCode` | int[] | VK codes per ASCII map below |
| `camera_position` | Vector3 | World coords (m) |
| `camera_rotation_oula` | Vector3 | [pitch, yaw, roll] degrees |
| `camera_rotation_quaternion` | Vector4 | [x, y, z, w] |
| `camera_Follow Offset` | Vector3 | Offset from player to camera |
| `camera_intrinsics` | Object | `{fx, fy, cx, cy}` (fx must equal fy) |
| `camera_speed` | Vector3 | m/s per axis |
| `player_position` | Vector3 | World coords (m) |
| `player_rotation_oula` | Vector3 | [pitch, yaw, roll] degrees |
| `player_rotation_quaternion` | Vector4 | [x, y, z, w] |
| `player_speed` | Vector3 | m/s per axis |
| `metric_scale` | float | World:real-world ratio |

## VK_TO_KEY map (subset)

| VK | Key |
|---|---|
| 87 | W |
| 65 | A |
| 83 | S |
| 68 | D |
| 16/160/161 | LSHIFT/RSHIFT |
| 17/162/163 | LCTRL/RCTRL |
| 18/164/165 | LALT/RALT |
| 32 | SPACE |
| 9 | TAB |
| (full table in adapter source) |

## Route types & input distribution

**Per batch of N clips, MUST satisfy:**
- 50 % normal-gameplay routes
- 50 % WASD-balanced (W=40 %, A/S/D = 20 % each)

**Route taxonomy:**
| Type | Description |
|---|---|
| 1 — normal | Player-style natural movement |
| 2 — special | Wall-hugging, ground-skimming, extreme angles |
| 3 — loop | Revisit start point in last 10 s |

## Behavior rules

- ≥ 90 % time = walking, running, view rotation
- ≤ 10 % time = stationary
- 0 % combat / dialog / inventory UI
- No mouse wheel zoom
- Camera & body transitions smooth (no warp)

## Depth requirements

- View-space linear depth (Z along optical axis)
- float32, single-channel "Z" in OpenEXR
- Unit: meters
- Invalid pixels (sky / clipped) = 0
- Sample rate: 6 fps (not 30)
- Filename: timestamp-aligned with video

## Acceptance gates

### Video gate (≤ 90 % pass = scene reject)

- 6 ≥ duration ≥ 5 min
- 30 fps stable, no dynamic FPS
- 1920×1080
- No UI / no logos / no dialog modals
- ≤ 2 NPC entities visible
- Stable scene flow, no portal cuts
- No 1st↔3rd person swap
- No dead/restart events

### action_camera gate (8-item check)

1. No missing fields, correct types
2. Coordinate alignment with our left-hand frame
3. mouse_dx/dy direction correct vs camera motion
4. No frame-skip or duplicate frames
5. keyCode timing matches visible action
6. Quaternion order = `[x, y, z, w]`
7. Speed values pass physical sanity
8. **`fx == fy`** in camera_intrinsics

### gameinfo gate

Operator-completed metadata, no missing fields.

### Depth gate

- float32 single-channel "Z"
- 6 fps sampling
- Invalid pixels = 0
- Filename matches video timestamps

## Current pipeline gap analysis (today)

| Spec gate | Pipeline status | Gap |
|---|---|---|
| 4 deliverables | ✅ adapter emits all | None |
| 20 fields | ✅ all present | None |
| `route_type` 1/2/3 | ❌ hardcoded `1` | **Need ScriptedProvider mode tag** |
| WASD distribution | ❌ `keyCode=[]` always | **Need keyCode emit + 40/20/20/20** |
| ≤ 10 % stationary | ⚠️ scripted noop=25 % | **Reduce to ≤ 5 %** |
| `fx == fy` | ✅ pinhole derivation | None |
| Quaternion `[x,y,z,w]` | ✅ adapter convention | None |
| Vector3 speed | ✅ today's fix | None |
| Real video | ❌ ffmpeg testsrc placeholder | **Phase 2 OBS spectator** |
| Real depth | ❌ 1801 hardlinked placeholder | **Phase 2 DepthAnything V2** |
| Game sound | ❌ silent testsrc | **Phase 2 with OBS** |

## Where to find this

- **Adapter source**: `src/oyster_agent_runner/buyer_spec_adapter.py`
- **Lint script**: `src/oyster_agent_runner/lint/lint_buyer_spec.py`
- **End-to-end test**: `bin/e2e_smoke.sh`
- **Production validator**: `bin/install_clean.sh`
- **One-line onboarding**: `bash SOP.sh`
