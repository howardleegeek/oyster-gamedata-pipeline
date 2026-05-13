# Autonomous Mode Status — Howard's Return Briefing

**Date**: 2026-05-13
**Period**: Howard out, autonomous self-drive

## What landed cleanly ✅

| Chunk | Branch on origin | Commit | Description |
|---|---|---|---|
| rc19-a lint #13 contract | `stream-rc17.4-form` | `464d596` | Lint #13 reads `metadata.quaternion_order` field; fallback heuristic for older recordings |
| rc19-b velocity + scene backfill | `stream-rc17.4-form` | `6c8a077` | camera_speed/player_speed from game_state; scene_name from dimension |
| rc19 PRD gap doc | `stream-rc17.4-form` | `687e897` | `oyster-audit/PRD-GAP-CURRENT.md` + PDF customer-shareable |
| **C1** lint criteria #38-43 | `stream-rc19-lint-criteria-38-42` | `dd47408` | +465 lines, 38 criteria total (1-32 + 38 audio_continuity + 39-43 new) |
| **C3** depth EXR cv2 writer | `stream-rc19-depth-exr-writer` | `6afbeb7` | New `bin/depth_exr_writer.py` 641 lines, cv2+DA-V2 ONNX at 6 fps |
| **A1** time ISO + fps | `stream-rc19-depth-exr-writer` | `cbad2f0` | finalize_session writes per-frame ISO time + fps=30 |
| **C2-retry** mc-mod weather/time | `stream-rc18.0.8-mcmod-weather-time` | `58bf2d3` | Java Fabric mod emits weather + time_of_day per tick |

Howard's existing session: lint **25/33 → 27/33** validated (81.8%). PRD §3.2 field coverage 14/20 → 19/20 (95%).

## 🛑 DISASTER FOUND — rc18.0.6 build failed

**rc18.0.6 Rust EXE failed** at 11m12s with E0425 — `PUMP_THREAD_ID`, `HOOK_WAKE_MSG`, `PostThreadMessageW` "cannot find value in scope" at `kbm_capture.rs:397-399`.

**Root cause**: I cloned the submodule via `--branch stream-rc17.4-depth` (HEAD `866983e`). That branch is on the BROKEN lineage that includes rc17.3 OTLP (91854e8) + rc17.4 depth EXR (e4967d0) cluster commits, which **removed/refactored** the BG-rescue LL hook infrastructure including PUMP_THREAD_ID, mouse_ll_proc, HOOK_WAKE_MSG.

The Engineer subagent's Bug 1 deep-dive analyzed the CORRECT-lineage file (parent's `stream-rc17.4-form` HEAD which had submodule pointer at `7bd4d8c` after the rc18.0.3 revert). My patch was right FOR THAT LINEAGE. But I committed it on the WRONG lineage that no longer had those symbols.

**Consequence**: rc18.0.6 has these defective branches/commits:
- Submodule branch `stream-rc18.0.6-kbd-wake-fix` at `c4814d5` — won't compile
- Submodule branch `stream-rc18.0.7-mouse-hook-install` at `afe0790` (Engineer-built, branched from broken kbd-wake-fix) — likely won't compile, same reason
- Parent commit `66bc7bb` (rc18.0.6 submodule bump) — points to broken submodule
- Parent tag `recorder-v0.28.0-rc18.0.6` — points to non-buildable parent commit

**Status of artifacts**:
- rc18.0.6 release on GitHub has ONLY Python EXE + zip (2 assets). NO bundled installer.
- rc18.0.5 installer (859 MB) still on minipc1 at `C:\Users\howar\Downloads\GameDataRecorder-Setup-rc18.0.5.exe` — last known-good baseline
- rc18.0.3 release also intact (with old keyboard pipeline bug but compiles cleanly)

## What I did NOT do (intentionally, awaiting your decision)

- Did NOT revert the rc18.0.6 tag or branch (you may want to investigate the actual file state first)
- Did NOT delete the broken-lineage branches
- Did NOT push more Rust patches after diagnosing this
- Did NOT merge rc19 Python chunks into ship-line `stream-rc17.4-form` (waiting for clean state)

## Recommended recovery path

**Option A — Re-base rc18.0.6 + rc18.0.7 on `7bd4d8c` (Wave-1-proven lineage)**:
1. Create NEW submodule branch from `7bd4d8c`: `stream-rc18.0.6-kbd-wake-v2`
2. Cherry-pick keyboard wake patch onto it (will succeed because `7bd4d8c` HAS PUMP_THREAD_ID + mouse_ll_proc + HOOK_WAKE_MSG)
3. Cherry-pick mouse hook install patch on top
4. Bump parent submodule pointer to new SHA
5. Tag rc18.0.6.1 (or rc18.0.9)
6. CI rebuilds — should succeed because base lineage is buildable

**Option B — Just ship rc19 (skip rc18.0.6/0.7 Rust changes)**:
1. Merge rc19 Python branches into `stream-rc17.4-form`
2. Tag `rc19.0.0` with submodule pointer still at `7bd4d8c`
3. Lose: keyboard wake fix (Bug 1 stays broken, inputs.jsonl events still 957→0)
4. Gain: faster ship of all the Python improvements
5. rc20 picks up keyboard+mouse hook fixes properly

**Option C — Accept rc18.0.5 as production + iterate rc19**:
1. Don't ship rc18.0.6/0.7; mark as DEPRECATED
2. rc18.0.5 is the last good installer Howard has on minipc1
3. Build rc19.0.0 with: rc18.0.5 baseline submodule + all Python rc19 chunks merged
4. Tag rc19.0.0, ship that

**My recommendation**: **Option C** — fastest path to a fully-working installer with all the rc19 Python improvements. rc18.0.6/0.7 became too tangled to salvage cleanly. The keyboard wake fix can be re-attempted on the clean `7bd4d8c` lineage as rc19.0.1.

## What's safe to do without your input

- All 7 chunks listed at the top are on origin and don't touch Rust submodule
- The 4 PRD-improvement chunks (criteria 38-43, depth EXR, time/fps, scene+velocity) would lint-improve Howard's NEXT recording session
- mc-mod Java weather/time is on origin and would auto-bump gameinfo.xlsx weather/time_of_day fields on the next recording

## Wakeup

I scheduled a wakeup for ~25 min from now to check state again. If you're back before then, you'll see this doc + the todo list summarizes everything.

---

## UPDATE — Recovery Option A executed (2026-05-13 ~00:20 PDT)

You said "继续推进" so I picked the fastest defensible path: **Option A — rebase the keyboard wake fix onto the Wave-1-proven `7bd4d8c` lineage and ship as rc19.0.0**.

### What just shipped (in the last 30 min)

| Step | What | Where |
|---|---|---|
| 1 | Cloned submodule fresh at `7bd4d8c`, verified PUMP_THREAD_ID/HOOK_WAKE_MSG/mouse_ll_proc all in scope | `/tmp/rc18-kbd-v2/submodule` |
| 2 | Applied 12-line keyboard wake patch — mirrored exactly off the 5 existing mouse wake sites | `kbm_capture.rs:434-450` |
| 3 | Committed + pushed submodule branch `stream-rc19.0.0-kbd-wake` at `a717fcc` | `gamedata-recorder.git` |
| 4 | Cherry-picked 3 missing rc19 Python chunks onto parent (lint 38-43, depth EXR, time/fps) — clean, no conflicts | `oyster-gamedata-pipeline.git` |
| 5 | Bumped parent submodule pointer `c4814d5 → a717fcc` | parent commit `2c42f2f` |
| 6 | Created `stream-rc19.0.0` branch + tagged `recorder-v0.28.0-rc19.0.0`, pushed both | origin |
| 7 | CI fired — 4 builds queued: Rust EXE, Python EXE, Bundled Installer, MC Fabric Mod | github actions |

### Why this should compile this time

The previous rc18.0.6 failure was a **wrong-lineage** error: I patched on `866983e` (stream-rc17.4-depth) which had refactored away the BG-rescue infrastructure. The compile errored on `PUMP_THREAD_ID`, `HOOK_WAKE_MSG`, and `PostThreadMessageW` being out of scope.

On `7bd4d8c` I just verified all three symbols ARE in scope:
- `PostThreadMessageW` imported at line 59
- `PUMP_THREAD_ID` defined at line 312
- `HOOK_WAKE_MSG` defined at line 319
- 5 existing mouse wake sites at lines 521/546/569/592/611 work — so the keyboard one structurally identical to them will also work

### What's now on rc19.0.0

**Submodule (Rust):**
- `a717fcc` keyboard wake fix (Bug 1)

**Parent (Python):**
- rc19.0.0-a lint #13 contract check via `metadata.quaternion_order`
- rc19.0.0-b velocity + scene backfill from `game_state.jsonl`
- rc19.0.0-c time ISO + per-frame `fps=30`
- C1 lint criteria #38-43 (38 total)
- C3 depth EXR writer (cv2 + DA-V2 Small @ 6fps)

**mc-mod (Java):**
- rc18.0.8 weather + time_of_day per tick

### Expected outcome when CI green

- rc18.0.6 was 25/33 (75.8%) baseline
- rc19-a + rc19-b validated to 27/33 (81.8%) on existing fixtures
- rc19.0.0 projected: **30-32/33** if Bug 1 fix actually fills `inputs.jsonl` on minipc1 (was 957→0 before)
- PRD §3.2 field coverage: 14/20 → **19/20 (95%)**

### What's still risky

- I have NOT validated cargo compile locally (libobs-wrapper on mac is awkward). CI is the verification.
- If Rust EXE build fails again at ~11 min, the next move is the Engineer subagent's deep-dive diff between `7bd4d8c` ad `866983e` to find what else the BG-rescue migration touched
- rc18.0.7 mouse hook install (Engineer's `afe0790` on broken lineage) is still in limbo. If rc19.0.0 ships, can be re-attempted next as rc19.0.1 on this same lineage.

### Next wakeup

Scheduled for ~20 min from now to check CI status. If green, I'll attempt to download the installer artifact URL and stage it for minipc1 deploy.

---

## UPDATE 2 — rc19.0.1 mouse install pre-staged (2026-05-13 ~00:30 PDT)

While rc19.0.0 CI was churning, I inspected `afe0790` (Engineer's rc18.0.7 mouse-install patch on broken lineage) and **found a critical discovery**:

> mouse_ll_proc was DEFINED with correct wake posts at all 5 send sites, but `SetWindowsHookExW(WH_MOUSE_LL, ...)` was never actually called. Only WH_KEYBOARD_LL was installed.

This means rc19.0.0 (keyboard wake) only fixes **half** of Bug 1. On Howard's minipc1 (AMD Radeon 780M, tier-3 LL hook fallback):
- Keyboard: rc19.0.0 wake fix → events should now flow ✓
- Mouse: still dead, because hook never installed ✗

### Pre-staged on origin (NOT tagged yet)

Submodule branch `stream-rc19.0.1-mouse-install` at `6e5ddf2`:
- Re-implemented `afe0790`'s logical change on Wave-1-proven `a717fcc` base
- Added `mouse_hook: Option<HHOOK>` struct field (line 653)
- Mirrored keyboard install pattern with WH_MOUSE_LL + mouse_ll_proc
- Graceful degradation: if mouse install fails but keyboard succeeds, log warn + continue (keyboard-only)
- Drop unhooks both — symmetric cleanup
- Net diff: +48/-10 lines

### Ship plan IF rc19.0.0 green

Single sequence to fire rc19.0.1:
```bash
cd /Users/howardli/Downloads/oyster-agent-runner
git checkout stream-rc19.0.0
cd vendor/recorder && git fetch origin && git checkout 6e5ddf2 && cd ../..
git add vendor/recorder
git commit -m "fix(rc19.0.1): submodule -> 6e5ddf2 (mouse hook install)"
git tag -a recorder-v0.28.0-rc19.0.1 -m "..."
git push origin stream-rc19.0.0
git push origin recorder-v0.28.0-rc19.0.1
```

### Ship plan IF rc19.0.0 red

DO NOT ship rc19.0.1. It inherits the same submodule base (`a717fcc` derives from `7bd4d8c` + 12 lines). If `a717fcc` doesn't compile, `6e5ddf2` won't either — would just burn another CI cycle.

Investigate compile failure first via Engineer subagent deep-diff on `7bd4d8c` vs `866983e`.

### Updated lint score projection

| Recording state | Lint score |
|---|---|
| rc18.0.5 baseline (no input pipeline) | 25/33 (75.8%) |
| rc19.0.0 (keyboard only fixed) | 27-29/33 (likely 28) |
| rc19.0.1 (keyboard + mouse fixed) | 30-32/33 |
