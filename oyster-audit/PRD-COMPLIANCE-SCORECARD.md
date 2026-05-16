# PRD Compliance Scorecard (2026-05-16)

> Code-level audit of the 83-item MECE checklist (`PRD-COMPLIANCE-MECE.md`).
> Updated each release. **Real-recording verification** moves ❓ → 🟢/🔴.

## Headline

**🟢 54/83 (65%) code-green** · **🔴 6/83 (7%) known-bad** · **❓ 23/83 (28%) unverified**

## Per-group breakdown

| Group | Total | 🟢 | 🔴 | ❓ | Status |
|---|---:|---:|---:|---:|---|
| A — files present | 5 | 5 | 0 | 0 | 100% |
| B — mp4 properties | 8 | 5 | 0 | 3 | B7/B8 audio-continuity + non-testsrc need real recording |
| C — 20 field-name literals | 20 | 8 | **5** | 7 | iron-law fixes needed in `action_camera_writer.rs` |
| D — value constraints | 10 | 4 | 0 | 6 | depend on C; verify after C fixed |
| E — coord system | 5 | **5** | 0 | 0 | rc19.0.3 done |
| F — gameinfo 14 fields | 14 | **14** | 0 | 0 | `generate_gameinfo.py` all present |
| G — rc19.0.3 gameinfo extras | 5 | **5** | 0 | 0 | `_augment_gameinfo_coords()` |
| H — depth EXR | 6 | **6** | 0 | 0 | `depth_exr_writer.py` complete |
| I — route diversity | 3 | 0 | 1 | 2 | I3 cyclic recording not implemented |
| J — quality (operator-side) | 7 | 2 | 0 | 5 | mostly operator-dependent |
| **TOTAL** | **83** | **54** | **6** | **23** | — |

## The 6 known 🔴 (in Group C — `action_camera_writer.rs`)

| ID | PRD field name | Writer emits | Fix | RFC scope |
|---|---|---|---|---|
| C5 | `mouse_x` | `mouseX` | `#[serde(rename = "mouse_x")]` | 1 RFC |
| C6 | `mouse_y` | `mouseY` | same | 1 RFC |
| C13 | `camera_Follow Offset` (space + capital F) | `Follow_Offset` | needs outer wrapper rename + `camera_` prefix | 1 RFC |
| C14 | `camera_intrinsics.Cx` / `.Cy` | `cx` / `cy` | Rust struct fields: `Cx: f64, Cy: f64` + serde rename | 1 RFC |
| C11 | `camera_rotation_oula` (prefixed) | `rotation_oula` (un-prefixed) | wrapper rename | shares RFC w/ C13 |
| C17 | `player_rotation_oula` | same | same | shares RFC w/ C13 |

**5 cluster RFCs total** (3 single-rename + 1 wrapper-rename for prefix + 1 intrinsics capitalization).

## The 23 ❓ unverified items

- B7, B8 (audio continuous + non-testsrc) — need real recording + ffprobe analysis
- C1, C2, C3, C4, C7, C8 (frame, time, fps, route_type, mouse_dx, mouse_dy presence) — verified by reading real recording's keys
- D2-D7, D9, D10 (value ranges) — read 9000 frames + check ranges
- I1, I2 (route + WASD balance per session) — analyze inputs.jsonl
- J1, J3, J4, J5, J6 (operator behavior) — manual review

**All 23 close in 1 step: Howard records 1 minute on minipc2 → audit tool runs → ✅/❌ per item.**

## 补全 plan (3 steps)

### Step 1 — fix the 6 🔴 (cluster RFCs)

Each RFC has:
- spec.md: rename one field via serde
- gate.sh: assert `grep "<new_name>"` in writer output JSON + Rust unit tests pass
- worktree off rc19.0.3.2

Estimate: 5 RFCs × ~30 min cluster = ~2.5h autonomous; ship rc19.0.4

### Step 2 — record + audit (verify 23 ❓)

- Howard: install rc19.0.4 on minipc2 + record 1 min MC
- I: run `bin/prd_compliance_audit.py <session_dir>` (when cluster ships it)
- Result: 23 ❓ → 🟢/🔴 split

### Step 3 — fix residual reds + ship rc19.0.5

Expected residual: ~3 items (I3 cyclic, J operator-dep, maybe 1 surprise from Step 2).

## History context (why we're here)

3 prior efforts touched recorder schema:
- `feat(prd-100)` — expanded action_camera but missed serde renames
- `feat(rc17.2)` — added recordDpi/intrinsics/quaternions
- `feat(rc17.3.1)` — gameinfo schema + depth disabled

Memory: `feedback_struct_field_addition_pitfall.md` warns "加 struct field 必须 grep 所有 literal callsite" — the surviving 🔴s are exactly that: struct fields added but rename attributes missed.

## Snapshot history

| Date | Tag | 🟢 | 🔴 | ❓ | Note |
|---|---|---:|---:|---:|---|
| 2026-05-16 | rc19.0.3.2 + audit | 54 | 6 | 23 | this scorecard, code-level only |
