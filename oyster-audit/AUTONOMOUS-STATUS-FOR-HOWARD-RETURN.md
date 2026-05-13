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
