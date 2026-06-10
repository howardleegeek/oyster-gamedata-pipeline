# PRD Compliance — INTEGRATED MECE (2026-05-16)

> Merged from: `RECORDER_BUYER_GAP_AUDIT.md` (severity-graded) + `PRD-COMPLIANCE-MECE.md` (83-item) +
> `BUYER_SPEC_V1.md` (Howard's 2026-05-02 paste) + `PRD_AUDIT_2026_05_04.md` (12 bugs) +
> `CURRENT_GAPS_2026_05_05.md` (scope handoff).
>
> **MECE**: each item is the unique check site for one PRD requirement.
> **Severity**: 🔴 buyer-rejects / 🟠 buyer-accepts-w/-workaround / 🟡 polish.
> **Source codes**: `RBGA-Xn` = RECORDER_BUYER_GAP_AUDIT, `MECE-Xn` = my 83 list, `PRDAUD-n` = PRD_AUDIT bug.
> **🚨 contradictions** flagged where docs disagree — Howard call needed.

---

## TOTAL: ~92 unique items across 11 groups

| Group | Count | Owner | Notes |
|---|---:|---|---|
| F — Files present | 8 | recorder | Includes intrinsics.yaml + MANIFEST.json (RBGA additions) |
| V — mp4 video properties | 8 | recorder+OBS | All static-checkable via ffprobe |
| A — action_camera content | 24 | recorder | 20 fields + 4 RBGA semantic gaps |
| D — action_camera value constraints | 10 | finalize | frame continuity, ranges, norms |
| C — coordinates / units | 6 | finalize | 🚨 systeminfo schema doc contradiction |
| G — gameinfo.xlsx | 16 | finalize+operator | 14 PRD + 2 real-value gaps |
| X — gameinfo rc19.0.3 extras | 5 | finalize | already 🟢 in rc19.0.3 |
| H — depth EXR | 7 | depth_exr_writer | 6 props + 1 RBGA real-depth |
| U — audio | 4 | recorder | RBGA B1-B4 |
| M — metadata / provenance | 6 | recorder | RBGA C1-C6 |
| Q — quality / behavior | 10 | operator+recorder | RBGA D/E/G + my J |

---

## Doc contradiction (🚨 Howard call needed)

**systeminfo.json schema**:

| Source | Field count | Includes `map_scale` + `map_bounds`? |
|---|---|---|
| `docs/BUYER_SPEC_V1.md` (Howard 2026-05-02 paste) | 7 | **YES** (literal in spec) |
| `docs/PRD-DATA-REQUIREMENTS.md` §4 | 5 | **NO** (calls them sample-error) |
| `docs/PRD_AUDIT_2026_05_04.md` F3 false-positive | 5 | **NO** (re-reading PRD page 3-4) |

**My read**: 2 sources say 5, 1 says 7. **Default to 5 unless Howard overrides**. But this item C8 stays 🚨 until resolved.

---

## Group F — Files present (8 items)

| ID | Item | Severity | Source | Status |
|---|---|---|---|---|
| F1 | `recording.mp4` exists | 🔴 | MECE-A1 | 🟢 |
| F2 | `action_camera.json` exists, non-empty JSON array | 🔴 | MECE-A2 | 🟢 |
| F3 | `gameinfo.xlsx` exists | 🔴 | MECE-A3 | 🟢 |
| F4 | `depth/` directory with ~1800 EXR | 🔴 | MECE-A4 | 🟡 (rc19.0.3 depth_exr_writer added, untested) |
| F5 | `systeminfo.json` exists | 🔴 | MECE-A5 | ❓ |
| F6 | `metadata.json` (per lint #22) | 🟠 | RBGA-C1 | ❓ |
| F7 | `intrinsics.yaml` (legacy lite recorder) | 🟡 | RBGA-A4 | ✅ already in v0.18.0 |
| F8 | `MANIFEST.json` with sha256 per file | 🟠 | RBGA-C4 | 🔴 not implemented |

## Group V — mp4 properties (8 items)

| ID | Item | Severity | Source | Status |
|---|---|---|---|---|
| V1 | Resolution 1920×1080 | 🔴 | MECE-B1 | 🟢 (OBS config) |
| V2 | Frame rate 30 fps stable | 🔴 | MECE-B2 | 🟢 (OBS config) |
| V3 | Duration in [300, 360] sec | 🔴 | MECE-B3 | 🟢 (rc19.0.2.3 5-min cap) |
| V4 | Codec H.264 or HEVC | 🟠 | MECE-B4 | 🟢 |
| V5 | Bitrate ≤ 12 Mbps; CRF ≤ 23 | 🟡 | MECE-B5 | 🟢 |
| V6 | AAC audio stream present | 🟠 | MECE-B6 | 🟢 |
| V7 | Audio continuous (no silence > 2s) | 🟠 | MECE-B7 + RBGA-B3 | ❓ |
| V8 | Real game footage (not testsrc) | 🔴 | MECE-B8 + PRDAUD-8 | ❓ |

## Group A — action_camera.json content (24 items)

20 field-presence checks (A1-A20) + 4 RBGA semantic gaps (A21-A24).

| ID | PRD literal field | Severity | Status |
|---|---|---|---|
| A1 | `frame` | 🔴 | 🟢 (rc19.0.4 after PR #14) |
| A2 | `time` | 🔴 | 🟢 (rc19.0.4) |
| A3 | `fps` | 🔴 | 🟡 finalize fills 30.0 |
| A4 | `route_type` | 🔴 | 🟡 default 1; cycle (I3) not impl |
| A5 | `mouse_x` | 🔴 | 🟢 (PR #14) |
| A6 | `mouse_y` | 🔴 | 🟢 (PR #14) |
| A7 | `mouse_dx` | 🔴 | 🟢 |
| A8 | `mouse_dy` | 🔴 | 🟢 |
| A9 | `keyCode` (int, NOT list) | 🔴 | ❓ PRDAUD-3 says was list; need re-verify |
| A10 | `camera_position` | 🔴 | 🟢 (rc19.0.3 backfill) |
| A11 | `camera_rotation_oula` (literal `oula` 拼音) | 🔴 | 🟢 (PR #14) |
| A12 | `camera_rotation_quaternion` [x,y,z,w] | 🔴 | 🟢 |
| A13 | `camera_Follow Offset` (literal SPACE + 大写 F) | 🔴 | 🟢 (PR #14) |
| A14 | `camera_intrinsics.Cx/Cy` (CAPITAL) | 🔴 | 🟢 (PR #14) |
| A15 | `camera_speed` | 🔴 | 🟢 (rc19.0.3 m/s ×20) |
| A16 | `player_position` | 🔴 | 🟢 |
| A17 | `player_rotation_oula` | 🔴 | 🟢 (PR #14 added field) |
| A18 | `player_rotation_quaternion` | 🔴 | 🟢 |
| A19 | `player_speed` | 🔴 | 🟢 |
| A20 | `metric_scale` | 🟠 | 🟢 |
| **A21** | **Real 6DoF camera position** (not placeholder 0.0) | 🔴 | RBGA-A2 — needs MC mod IPC; PARTIAL via rc19.0.3 |
| **A22** | **Real camera rotation** (not identity quaternion) | 🔴 | RBGA-A3 — needs MC mod IPC; PARTIAL |
| **A23** | **9000 frame-aligned records** (not raw input event count) | 🟠 | RBGA-A5 — needs frame-rate resampling |
| **A24** | **mouse_x/y = look-vector** (not screen coords) | 🟠 | RBGA-A6 — needs delta-yaw/pitch conversion |

## Group D — action_camera value constraints (10 items)

| ID | Constraint | Status |
|---|---|---|
| D1 | frame continuity 0..N-1 | 🟢 |
| D2 | N ≈ 9000 (overlaps A23) | 🟡 |
| D3 | fps == 30.0 per frame | 🟢 |
| D4 | route_type ∈ {1,2,3} | 🟡 |
| D5 | mouse_x/y ∈ [0,1] | 🟢 |
| D6 | Vec3/Vec4 length check | ❓ |
| D7 | quaternion norm ≈ 1 | 🟢 |
| D8 | fx == fy (pinhole) | 🟢 |
| D9 | speed magnitudes ≤ 100 m/s | 🟢 |
| D10 | pitch [-90,90], yaw/roll [-180,180] | 🟢 |

## Group C — Coordinates + units + cross-file (6 items)

| ID | Item | Status |
|---|---|---|
| C1 | Left-handed coord system | 🟢 (rc19.0.3 yaw-negate) |
| C2 | Units = meters | 🟢 |
| C3 | yaw negated MC→Buyer | 🟢 |
| C4 | quat order xyzw | 🟢 |
| C5 | velocity m/s (blocks/tick × 20) | 🟢 |
| **C6** | **🚨 systeminfo schema (5-or-7 fields) — doc contradiction** | 🚨 |

## Group G — gameinfo.xlsx 14 fields (16 items)

| ID | Field | Status |
|---|---|---|
| G1-G14 | 14 PRD fields | 🟢 in generate_gameinfo.py (gold sample stale) |
| **G15** | **operator_id traceable to tester** (not "lite-recorder") | RBGA-C2 🔴 needs config/prompt |
| **G16** | **character_name from MC launcher_profiles.json** (not "DataPilot") | RBGA-C3 🔴 needs MC integration |

## Group X — gameinfo.xlsx rc19.0.3 extras (5 items)

| ID | Row | Status |
|---|---|---|
| X1-X5 | world_gravity / coord_system / velocity_unit / mc_blocks / mc_ticks | 🟢 rc19.0.3 _augment_gameinfo_coords() |

## Group H — depth EXR (7 items)

| ID | Item | Status |
|---|---|---|
| H1 | 1800 files named 000000-001799 | 🟢 code |
| H2 | 6 fps sampling | 🟢 |
| H3 | 1920×1080 | 🟢 |
| H4 | float32 single-channel | 🟢 |
| H5 | invalid pixels = 0 | 🟢 |
| H6 | values in metric meters | 🟢 |
| **H7** | **Real depth values** (not 16×16 zeros placeholder) | RBGA-A1 / PRDAUD-1 🔴 — rc19.0.3 wires DA-V2 inference |

## Group U — Audio (4 items)

| ID | Item | Severity |
|---|---|---|
| U1 | `audio_events.json` (footsteps/gunshots timestamps) | RBGA-B1 🟠 |
| U2 | Separate `audio.flac` (not just embedded in mp4) | RBGA-B2 🟡 |
| U3 | Mic-muted detection / warn pre-record | RBGA-B3 🟠 |
| U4 | Loopback-only audio (privacy: no mic by default) | RBGA-B4 🟠 |

## Group M — Metadata / provenance (6 items)

| ID | Item |
|---|---|
| M1 | metadata.json with timestamp/location/device_id/session_id (lint #22) — RBGA-C1 🟠 |
| M2 | session_id UUID4 unique cross-machine — RBGA-C6 🟡 |
| M3 | UTC timestamps everywhere — RBGA-C5 🟡 |
| M4 | MANIFEST.json sha256 per file (duplicate of F8) — RBGA-C4 🟠 |
| M5 | recorder version signed (cert dropped per Howard) — RBGA-F3 🟡 |
| M6 | log rotation at 10MB — RBGA-F4 🟡 |

## Group Q — Quality / behavior (10 items)

Operator-side + recorder-side gates against polluted data:

| ID | Item | Severity |
|---|---|---|
| Q1 | No UI popups (inventory/menu/settings on screen) | 🟠 |
| Q2 | No 1st↔3rd person switch | 🟠 |
| Q3 | No death/respawn | 🟠 |
| Q4 | No 1-min stationary periods | 🟠 |
| Q5 | Fullscreen MC (window covers 1920×1080) | 🟠 |
| Q6 | No macro / robotic input | 🟠 |
| Q7 | Native 30fps source (no downsample) | 🟠 |
| Q8 | Consent flow / EULA (RBGA-G2) | 🟠 |
| Q9 | Window-only capture (not desktop) — RBGA-G1 | 🟠 |
| Q10 | WASD balance / route diversity — covers MECE-I1+I2 | 🟠 |

---

## Aggregated status by tier

| Tier | Count | Notes |
|---|---:|---|
| 🟢 Confirmed green at code level | ~50 | PR #14 closes 6 iron-law; rc19.0.3 closes coord; generate_gameinfo handles 14 |
| 🟡 Implemented but unverified | ~15 | depth, finalize, OBS config — need real recording |
| 🔴 Real dev gaps | ~12 | A21/A22 (real 6DoF, real quat), G15/G16 (operator+character real), H7 (real depth), I3 cyclic, F8/M1-M4 metadata, F9/F14 |
| 🚨 Doc contradiction | 1 | C6 systeminfo schema |
| ❓ Untested | ~14 | A9/A23/A24, V7/V8, B/E quality items |

**Target 92/92 = ~50% green now → ~75% after PR #14 + real recording + metadata/manifest impl → ~88% after 12 dev gaps closed → 100% requires real engine telemetry (Replay Mod or full Rust integration per `RESEARCH_DEPTH_CAPTURE_MC.md`)**.

---

## Path to 100% (per RBGA recommended releases)

| Phase | Closes | Effort |
|---|---|---|
| rc19.0.4 (PR #14 merge + submodule bump) | A5/A6/A11/A13/A14/A17 = 6 iron-law | Done in PR |
| rc19.0.5 (FOV from options.txt, real operator/character) | F7/G15/G16 = RBGA A4/C2/C3 | 2h |
| rc19.0.6 (metadata.json + MANIFEST.json) | F6/M1/M4 = RBGA C1/C4 | 2h |
| rc19.1.0 (frame-align resample) | A23 = RBGA A5 | 1day |
| rc19.2.0 (real 6DoF via MC mod IPC) | A21/A22/H7 = RBGA A1/A2/A3 | 1week — biggest |
| Howard team UI work | F8 + G15 operator UI + Q8 consent | per Howard scope |

After rc19.2.0: ~84/92 (~91%) — remaining 8 are operator-behavior (Q1-Q10) that can ONLY be enforced via human review, not automation.
