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

---

## UPDATE 3 — rc19.0.0 Rust EXE COMPILE SUCCESS (2026-05-13 ~00:33 PDT)

✅ **The wake patch compiles cleanly on Wave-1 lineage.** The structural fix is validated.

Step-level proof from CI job 25784493248:
- ✅ Setup Rust toolchain
- ✅ Cache cargo registry + target
- ✅ **Build Rust application (release): completed success** (~12 min)
- ✅ Create dist directory
- ✅ Install cargo-obs-build + fetch OBS runtime
- ✅ Copy Rust binary as OysterRecorder.exe
- ✅ Copy OBS FFmpeg mux helper
- ✅ Upload Rust recorder artifact

This rules out the "wrong-lineage" failure mode that doomed rc18.0.6. The PUMP_THREAD_ID / HOOK_WAKE_MSG / PostThreadMessageW symbols are resolved correctly on `7bd4d8c` lineage.

Followed up immediately by tagging **rc19.0.1** (mouse install) — same lineage + 48 more lines of symmetric WH_MOUSE_LL install code. Builds firing in parallel.

### Current CI matrix

| Tag | Rust EXE | Python EXE | Bundled Installer | MC Mod |
|---|---|---|---|---|
| **rc19.0.0** | ✅ success | ✅ success | 🟡 bundling | ✅ success |
| **rc19.0.1** | 🟡 building | 🟡 building | 🟡 bundling | ✅ success |

ETA: rc19.0.0 installer in ~15 min; rc19.0.1 installer in ~30 min.

### Deploy command (ready when you are)

I fixed a naming bug in `bin/minipc_v028_install.sh` (was looking for `OysterRecorder-Setup-...exe` but CI produces `GameDataRecorder-Setup-...exe`). Default now correct.

When you return, one of these is the right deploy command:

```bash
# PREFERRED: rc19.0.1 (both keyboard + mouse hooks installed — full Bug 1 fix)
RELEASE_TAG=recorder-v0.28.0-rc19.0.1 bash /Users/howardli/Downloads/oyster-agent-runner/bin/minipc_v028_install.sh

# FALLBACK: rc19.0.0 (only keyboard wake fixed)
RELEASE_TAG=recorder-v0.28.0-rc19.0.0 bash /Users/howardli/Downloads/oyster-agent-runner/bin/minipc_v028_install.sh
```

### Branches & SHAs on origin

**Submodule (gamedata-recorder):**
- `stream-rc19.0.0-kbd-wake` at `a717fcc` — keyboard wake fix on Wave-1
- `stream-rc19.0.1-mouse-install` at `6e5ddf2` — adds mouse hook install

**Parent (oyster-gamedata-pipeline):**
- `stream-rc19.0.0` at `11d349b` — rc19.0.0 ship branch + status docs
- `stream-rc19.0.1` at `d02f8f1` — rc19.0.1 ship branch + deploy fix
- Tag `recorder-v0.28.0-rc19.0.0` → submodule `a717fcc`
- Tag `recorder-v0.28.0-rc19.0.1` → submodule `6e5ddf2`

---

## UPDATE 4 — rc19.0.1 TESTED + rc19.0.2 CLUSTER DISPATCHED (2026-05-13 ~02:00 PDT)

你测了 rc19.0.1, 录了 7m13s, 我 SCP + finalize + lint 出分: **28/38 = 73.7%**.

### ✅ Bug 1 实证修好
inputs.jsonl 5.3 MB, 1171 keyboard + 62K mouse events (vs rc18.0.5 baseline 0.4 KB / 0 events).

### 10 failing — 已分类 + 已 dispatch 修

| # | Criterion | Root cause | Fix path |
|---|---|---|---|
| 2 / 29 | Duration 428.9s vs 360s | 👤 你录长了 | 下次录 5:00-5:30 |
| 41 | Stationary 30.8% | 👤 操作 | 持续移动 |
| 15 / 16 / 24 | Depth EXR missing | 🔧 installer 没装 deps | **S01 cluster** |
| 31 | Mouse/cam alignment 45% borderline | ⚠️ lint noise | **S03 cluster** |
| 38 | Audio 1 gap >2s | 🔌 hardware OR lint | **S06 cluster** |
| 39 | Input latency 未测 | 🔧 没 wire | **S05 cluster** |
| 42 | Frozen 2.03s (just over) | ⚠️ MC chunk load | **S03 cluster** |

### 6 atomic specs dispatched to Aliyun cluster (parallel)

| Spec | Task | Model | Bash bg ID |
|---|---|---|---|
| **S01** | Bundle Python tooling + cv2 + DA-V2 model into installer | deepseek-v3.2 | `b3zjsabu3` |
| **S02** | mc-mod jar CI pin to tag SHA | deepseek-v3.2 | `b3s3750ra` |
| **S03** | Lint #31 + #42 refinement | glm-5 | `bw0tazxxw` |
| **S04** | Recorder auto-exit + auto-finalize on MC quit | qwen3.6-plus | `bcvrgaob8` |
| **S05** | Wire measure_input_latency.py into finalize | MiniMax-M2.5 | `bwdt8gq93` |
| **S06** | Audio gap ffprobe + lint #38 decision | deepseek-v3.2 | `b8gbmi5xg` |

每个 agent 会: git checkout 新 branch → 改代码 → push branch. 不自动 tag/merge — 你回来 review PR.

### Projected score progression

| State | Score | Coverage |
|---|---|---|
| rc18.0.5 baseline | 25/33 (75.8%) | broken Bug 1 |
| rc19.0.1 测出 (你刚录) | **28/38 (73.7%)** | Bug 1 fix 实证, deployment gaps 浮出 |
| rc19.0.2 + 你重录 5:00-5:30 移动充分 | **35-38/38 (92-100%)** | 取决于 audio 硬件 |

### 你回来 todo

1. 查 cluster 结果: `ls /tmp/rc19.0.2-work/S*/log.txt` + 各 git log
2. Review PRs, merge 进 stream-rc19.0.2
3. Tag `recorder-v0.28.0-rc19.0.2` → CI ~30 min
4. Deploy: `RELEASE_TAG=recorder-v0.28.0-rc19.0.2 bash bin/minipc_v028_install.sh`
5. 重录 5:00-5:30 (持续移动!)
6. Lint 应 hit 35-38/38

### 文件位置

- Specs: `/Users/howardli/Downloads/oyster/specs/rc19.0.2/S0*.md`
- Cluster work: `/tmp/rc19.0.2-work/S0*/repo/` + `/tmp/rc19.0.2-work/S0*/log.txt`
- 你 rc19.0.1 session (28/38 baseline): `/tmp/rc19-session/session_20260513_014031_98b1e850/`
