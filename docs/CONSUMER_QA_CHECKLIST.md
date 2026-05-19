# Consumer-Side QA Checklist — 2026-05-05

> Howard: "我们的测试人员 会测试的就是 c端东西"
>
> Testers test as a **clueless EPal companion would**, not as engineers.
> No terminals. No commands. Just: download, click, play, observe.

---

## Tester profile this checklist assumes

- Owns Windows 11 (or 10) desktop
- Has Minecraft Java Edition 1.20.4 already installed (or will install via launcher)
- Has Steam / common gaming setup
- **Does NOT** know what a terminal, pip, npm, or `.msi` config flag is
- Reads only what's on screen; never opens documentation

---

## Test pass — one full session (30 minutes)

### Phase 1 — Install

| # | Step | Expected | Pass/Fail |
|---|---|---|---|
| 1.1 | Click download link → saves `OysterRecorder-Setup-v0.2.0.msi` | downloads cleanly, no SmartScreen warning | ☐ |
| 1.2 | Double-click `.msi` | Windows installer opens; shows "Oyster Recorder" with publisher name | ☐ |
| 1.3 | Click "Install" | progress bar finishes in <60 seconds | ☐ |
| 1.4 | Installer closes | system tray now shows oyster icon | ☐ |
| 1.5 | Right-click tray icon | menu shows: Status / Settings / Help / Quit | ☐ |

### Phase 2 — First-run splash

| # | Step | Expected | Pass/Fail |
|---|---|---|---|
| 2.1 | Splash window opens automatically | shows "Welcome — record your gameplay for AI training" | ☐ |
| 2.2 | Age confirmation checkbox | unchecked by default; must check 13+ to continue | ☐ |
| 2.3 | Region dropdown | defaults to detected region; user can change | ☐ |
| 2.4 | Per-game opt-in checkboxes | shows: Minecraft (✓ default on), other games coming soon | ☐ |
| 2.5 | "Continue" button | enabled only when age + at least 1 game checked | ☐ |
| 2.6 | Click Continue | splash closes; tray icon turns blue (idle) | ☐ |
| 2.7 | NO email asked | nothing about email or PayPal anywhere | ☐ |

### Phase 3 — Play Minecraft

| # | Step | Expected | Pass/Fail |
|---|---|---|---|
| 3.1 | Launch Minecraft 1.20.4 | game starts as usual; no popups from our app | ☐ |
| 3.2 | Within 5 seconds of game launch | tray icon turns red (recording); tooltip says "Recording: Minecraft" | ☐ |
| 3.3 | Play normally for 5+ minutes | game runs at usual FPS (no perceptible drop) | ☐ |
| 3.4 | Open Minecraft inventory / chat | recorder keeps recording; no error | ☐ |
| 3.5 | Alt-Tab to desktop briefly | tray icon still red; recording continues | ☐ |
| 3.6 | Quit Minecraft cleanly | within 5s, tray icon turns yellow (uploading or saving) | ☐ |
| 3.7 | After 30s | tray turns green; toast notification "✓ Clip saved" | ☐ |

### Phase 4 — Inspect the output (the only "file system" step)

| # | Step | Expected | Pass/Fail |
|---|---|---|---|
| 4.1 | Right-click tray icon → "Show Clips" | opens File Explorer at `%USERPROFILE%\Documents\OysterClips\` | ☐ |
| 4.2 | Folder contains | one file `clip-YYYYMMDD-HHMMSS.tar.gz` (~50-200 MB) | ☐ |
| 4.3 | File modified time | matches when the user quit Minecraft | ☐ |

### Phase 5 — Privacy controls

| # | Step | Expected | Pass/Fail |
|---|---|---|---|
| 5.1 | Tray menu → Settings → Privacy | dashboard opens in default browser at `localhost:NNNN/privacy` | ☐ |
| 5.2 | Dashboard shows | per-game opt-in toggles, "Delete my data" button, "What we record" explainer | ☐ |
| 5.3 | Toggle Minecraft to OFF | next game launch should NOT trigger recording | ☐ |
| 5.4 | Toggle back to ON | next launch DOES record | ☐ |
| 5.5 | Click "Delete my data" | confirmation dialog; on confirm, all clips in OysterClips/ are removed | ☐ |

### Phase 6 — Stress / edge cases

| # | Step | Expected | Pass/Fail |
|---|---|---|---|
| 6.1 | Play Minecraft for 30+ minutes | no tray icon stuck, no crash, no FPS drop | ☐ |
| 6.2 | Disconnect WiFi mid-recording | tray icon yellow with tooltip "offline, will retry" | ☐ |
| 6.3 | Reconnect WiFi | tray returns to normal flow within 30s | ☐ |
| 6.4 | Force-quit Minecraft (Task Manager) | recorder still cleanly finalizes; clip is salvaged | ☐ |
| 6.5 | Reboot Windows during recording | on next boot, tray comes up; old in-progress clip moved to "_recovered" subfolder | ☐ |

### Phase 7 — Uninstall

| # | Step | Expected | Pass/Fail |
|---|---|---|---|
| 7.1 | Windows Settings → Apps → uninstall "Oyster Recorder" | uninstaller runs; tray icon disappears | ☐ |
| 7.2 | Reboot | no tray icon at startup; no leftover service | ☐ |
| 7.3 | Check `%USERPROFILE%\Documents\OysterClips\` | exists or removed per uninstaller's choice (asked at uninstall) | ☐ |
| 7.4 | Check Windows registry under HKCU\Software | no orphan keys named Oyster | ☐ |

---

## Reporting a failure

If any row above fails, the tester:

1. Note the **row number** (e.g. 3.2)
2. Take a screenshot of the tray + any visible window
3. Right-click tray → Help → "Send error report" (auto-attaches recent log; goes to our error service per W28)
4. (Optional) Slack/email Howard with the row number

**No GitHub issue filing required.** Testers send a row number + screenshot; engineers triage from the error service dashboard.

---

## Pass criteria for v0.2.0 release

- **Hard gate**: Phase 1 + 2 + 3 + 4 must pass 100% on a clean Windows 11 install.
- **Soft gate**: Phase 5 (privacy) + Phase 7 (uninstall) must pass 100%.
- **Acceptable failures (post-MVP)**: Phase 6 (stress) — flag any failure but do NOT block release if Phase 1-4 pass.

If 5/7 EPal companions can complete Phase 1-4 without help, ship v0.2.0.

---

## Tester quick-reference card (printable)

```
TESTER ROLE: Pretend you've never seen this software before.
            Do NOT read documentation. Do NOT open a terminal.

WHAT TO DO:
  1. Download and double-click the .msi
  2. Click through the splash with default opt-in
  3. Play Minecraft 1.20.4 for 5+ minutes
  4. Quit Minecraft normally
  5. Check Documents/OysterClips/ for a new file
  6. Try the privacy dashboard from the tray
  7. Uninstall via Windows Settings

WHAT TO REPORT:
  - Row number that failed (e.g. "3.2 failed")
  - Screenshot of what was on screen
  - Tray menu → Help → Send error report
```

Print and hand to tester. No further explanation needed.

---

## Phase 4.5 — Auto PRD verification (hidden from tester, runs in background)

Howard: "录下来的数据都是针对 该有的我们都要用" — every clip MUST satisfy the Lark PDF spec (24 acceptance criteria, see `docs/PRD_AUDIT_2026_05_04.md`).

**Tester does NOT touch this.** The recorder + tray UI handle it transparently:

| What happens | Tester sees |
|---|---|
| Game ends | tray turns yellow (saving) |
| `bin/lint_v3_prd_grounded.py` runs on the new tarball | (invisible) |
| If 24/24 PASS | tray turns green; toast "✓ Clip saved" |
| If <24/24 | tray turns red; toast "Clip incomplete — auto-retrying next session"; clip moved to `_failed_lint/` subfolder; nothing for tester to do |

The recording side is supposed to ship 24/24 by construction (G161 real depth + G162 route diversity + G163 keycode int + G164 capital Cx/Cy intrinsics + G165 enforces). If lint fails, that's an engineering bug, not a tester action item.

### Tester's only PRD-related step

Phase 4.2 already covers it: file exists, size 50-200 MB, modified time matches game-quit. **If those three pass, the data integrity step PASSED for the tester's purposes.**

Engineers verify deeper via the error service (W28) which auto-receives any lint failure with the failing criterion + traceback.

### What the buyer receives

Each clip in `Documents/OysterClips/clip-YYYYMMDD-HHMMSS.tar.gz` is a 5-file delivery per Lark PDF:

```
clip-YYYYMMDD-HHMMSS.tar.gz
├── video.mp4              (H.265 1920×1080 30fps, 5-6 min duration)
├── systeminfo.json        (gameProcessName, x, y, width, height, recordDpi)
├── action_camera.json     (9000 records × 20 fields per PRD)
├── gameinfo.xlsx          (scene metadata)
└── depth/
    ├── depth_000000.exr   (1800 frames at 6fps, float32 single-channel Z, meters, invalid pixel = 0)
    ├── depth_000001.exr
    └── ... (1800 total)
```

Buyer pulls these from S3 → trains world model → done.
