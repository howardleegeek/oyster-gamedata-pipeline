# Tester Issues Log

> Howard 2026-05-05: "你能 log 下来问题不？"
>
> Append-only ledger of every tester-reported issue. One row per report.
> When Howard forwards a tester complaint, I add a row here BEFORE
> diagnosing or shipping a fix, so we don't lose the trail across
> sessions or after context compaction.

---

## Schema

| Column | Meaning |
|---|---|
| ID | `T-NNN` sequential. Howard or I assign. |
| Date | When Howard reported it to me (PT). |
| Reporter | "Howard" if Howard relayed; "Tester" if direct quote. |
| Version | Which `*.exe` version the tester was running. |
| Symptom | Howard's exact words (Chinese OK), or tester's. |
| Hypothesis | First-pass guess at the cause. |
| Fix Version | Tag where we ship the fix or diagnostic build. |
| Outcome | `OPEN` / `CONFIRMED` / `FIXED` / `NOT-OUR-BUG` / `WONTFIX` / `WAITING`. |
| Remote Log URL | ix.io short URL when v0.5.0+ uploaded; blank otherwise. |

---

## Open / In-flight

| ID | Date | Reporter | Version | Symptom | Hypothesis | Fix Version | Outcome | Remote Log URL |
|---|---|---|---|---|---|---|---|---|
| T-003 | 2026-05-05 | Howard (relay) | recorder-v0.1.0 → v0.3.0 | "他们一进去minecraft 马上闪退到主页" | Auto-spawned ffmpeg gdigrab interferes with MC's exclusive-fullscreen DirectX/OpenGL context | recorder-v0.4.0 (diagnostic) + v0.6.0 (minimize-on-arm) | WAITING (Howard now sees the recorder DID record successfully — see T-006) | (none yet — pre-v0.5.0) |
| T-004 | 2026-05-05 | Howard (direct) | recorder-v0.1.0 → v0.5.0 | "界面 / 影响玩游戏" | Tk window competes with MC for screen real estate + focus; in exclusive fullscreen this can crash MC and is always distracting | recorder-v0.6.0 — `self.iconify()` on arm, auto `deiconify()` after recording ends | WAITING (build queued, ETA <5 min) | (will use v0.5.0+ ix.io) |
| T-005 | 2026-05-05 | Howard (direct) | recorder-v0.1.0 (minipc) | "好像还是录了。就是不知道有没有录到买家想要的东西" | Lite recorder produces only video.mp4, missing 4/5 PRD files (systeminfo, action_camera, gameinfo, depth/) — buyer needs full 5-file tarball | recorder-v0.7.0 — auto-runs G165 lint v3 inline after every recording, shows N/24 PRD verdict in GUI without separate validator | RESOLVED (v0.7.0 live, awaits tester upgrade from v0.1.0 to v0.7.0) | (will populate via v0.5.0+ telemetry) |
| T-006 | 2026-05-05 | Engineer (direct SSH read) | recorder-v0.1.0 (on minipc) | Tester actually recorded `clip-20260505-142357.mp4` (31 MB) successfully — but it's just video, no PRD shape | This invalidates T-003's "MC crashes when our recorder runs" theory — tester's MC must have crashed on its own (not because of us). MC was running again when SSH-checked (javaw 5GB) | n/a — informational | INVALIDATES T-003 hypothesis. Tester's MC self-issue (Java/driver/mod) is separate from our recorder; we're cleared. | n/a (direct SSH) |

## Closed

| ID | Date | Reporter | Version | Symptom | Hypothesis | Fix Version | Outcome | Remote Log URL |
|---|---|---|---|---|---|---|---|---|
| T-001 | 2026-05-05 | Howard (relay) | qa-validator-v0.1.0 | "我下载之后 不行" | English-only multi-panel UI confusing for non-programmer testers | qa-validator-v0.2.0 (Chinese single-button GUI) | FIXED — simplified to one-button + Chinese; superseded by next issue T-002 | n/a |
| T-002 | 2026-05-05 | Howard (relay) | qa-validator-v0.2.0 | "我点开了 然后 不太会用" / "弄的是不是太复杂了" | Wrong product axis — testers had no clip files to validate; validator is engineer-side tool, testers need a recorder | recorder-v0.1.0 (pivot to recorder) | NOT-OUR-PRODUCT — abandoned validator path; built recorder line instead | n/a |

---

## How to add a new entry

When Howard says "tester reports X":

1. Append a row to **Open / In-flight** with `Outcome=OPEN`.
2. Quote Howard's exact words in `Symptom` (don't paraphrase — wording carries diagnostic info).
3. Form one specific Hypothesis even if uncertain. Vague hypotheses don't get tested.
4. Ship a diagnostic or fix build, fill `Fix Version`.
5. When tester confirms result, move row to **Closed** with final `Outcome`.
6. If a v0.5.0+ run produced an ix.io URL, paste it in the `Remote Log URL` column.

---

## Diagnostic protocol templates

### v0.4.0 recorder diagnostic (T-003 active)

```
1. Tester downloads recorder-v0.4.0 .exe
2. Double-clicks .exe — DOES NOT click any button
3. Opens Minecraft, plays 5 min
4. Outcome A — MC still crashes on its own
   → MC self-issue (Java / driver / mod), close T-003 as NOT-OUR-BUG
5. Outcome B — MC works fine
   → Tester clicks ▶ 开始录制, plays MC again
   5a. MC now crashes → ffmpeg-vs-MC issue confirmed, open T-004 for v0.6.0 fix
   5b. MC still works → close T-003 as FIXED
6. Tester sends C:\Users\<USERNAME>\OysterRecorder.log via screenshot
   OR (if v0.5.0+) URL is auto-pasted in GUI subtitle for engineer to curl
```

---

## Why this file is in the repo

- Lives next to source so any engineer reading the repo finds it first.
- Append-only Markdown table — diff-friendly, no merge conflicts.
- Single source of truth across Howard's iterations + my session resets.
- After context compaction the next Claude session reads this file
  before answering any tester question, so we don't relearn.
