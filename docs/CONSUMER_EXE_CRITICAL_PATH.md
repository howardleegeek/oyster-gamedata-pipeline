# Consumer .exe Critical Path — 2026-05-05

> Howard: "目前如果出新的release的话 就是出exe 可以让他们直接用起来录游戏视频和数据的软件"
>
> The next release IS the consumer .exe. Everything else waits.
> No more spec additions until this ships.

---

## The bar

A clueless EPal companion does this and only this:

1. Receives a link from EPal
2. Downloads `OysterRecorder-Setup-vX.Y.Z.msi` (~80 MB; Java + recorder + dependencies bundled)
3. Double-clicks the .msi
4. Clicks "Install" once
5. Click "Continue" on a single splash with default opt-in (Minecraft)
6. Sees system tray icon appear
7. Plays a regular EPal Minecraft session
8. Tray icon glows red while recording, green when uploading
9. Session ends → toast: "✓ Clip saved, $X bonus pending"
10. Done. Never opens a terminal. Never reads a runbook.

---

## Critical-path spec list (9 specs, ALL ALREADY QUEUED, ALL P0)

| # | Spec | What it produces | Cluster status |
|---|---|---|---|
| 1 | **G214** `installer_one_click_windows.py` | the .msi itself, Java bundled | pending |
| 2 | **G216** `onboarding_consumer_splash.py` | one-screen first-run splash | pending |
| 3 | **G217** `game_auto_detector.py` | Windows process scanner | pending |
| 4 | **G218** `auto_record_orchestrator.py` | game start → record, exit → upload | pending |
| 5 | **G219** `system_tray_consumer_ui.py` | tray icon + menu | pending |
| 6 | **G220** `consumer_privacy_dashboard.py` | per-game opt-in + delete-my-data | pending |
| 7 | **G221** `consent_log_signed.py` | legal floor (HMAC-signed consent) | pending |
| 8 | **G253** `epal_session_lifecycle_hook.py` | EPal session-start/end webhooks | pending |
| 9 | **G241** `code_signing_windows_authenticode.py` | sign the .msi to bypass SmartScreen | pending |
| 10 | **G243** `release_builder_consumer.py` | assembles all above into one signed .msi | pending |

**Plus the meta-gate**: G228 e2e_smoke must pass on a clueless-user simulation before tagging the release.

## What's NOT critical for first release

(Don't expand scope; let cluster drain the above first.)

- ❌ macOS .pkg (G215) — Windows-first; mac later
- ❌ Auto-updater (G225) — manual update for first batch of EPal beta
- ❌ Error reporting service (W28) — locally logs only for first batch
- ❌ Backend ingest (G190) — clips on flash drive / S3 manual upload first
- ❌ Vendor portal / earnings dashboard — EPal app shows the bonus
- ❌ Code signing for macOS (G242) — Windows-only first release
- ❌ i18n (G252) — English UI only; Chinese in next release
- ❌ FPS overhead monitor (G229) — important but not blocker for first beta
- ❌ Anomaly detection (G194) — manual quality review for first batch
- ❌ Multi-game beyond Minecraft — beta covers MC only; expand later

## Cluster execution plan

The 10 critical-path specs are atomic NEW-FILE units the cluster ships in parallel.
Velocity ~3-5 specs/min → **~3-5 minutes wall-clock to drain** (assuming no truncation issues).

After they all complete, manual integration step:
1. `python3 bin/release_builder_consumer.py --version 0.2.0` produces the .msi
2. `python3 bin/code_signing_windows_authenticode.py --input dist/OysterRecorder-Setup-0.2.0.msi`
3. Smoke-test on a fresh Windows VM (or minipc): does double-click → tray → opt-in → play MC → record → save the tarball?
4. Tag `v0.2.0-consumer-beta`, attach signed .msi, send to first 10 EPal companions

## What success looks like

Day 1: 10 EPal companions install, 8 of them produce ≥1 PRD-compliant clip during a paid session.
Day 7: 50 companions, 200+ clips, lint-PASS rate >90%.
Day 30: open to entire EPal companion base.

## Spec freeze

**No new specs until `v0.2.0-consumer-beta` ships.** Howard:
> "我们的核心问题就是这个软件必须简单用起来非常简单。这些人不懂技术。"

Adding more specs delays the .msi. Anything beyond the 10 critical-path specs goes into a "post-v0.2.0" bucket.

---

## UX-First principles (Howard 2026-05-05)

> "我们之前的设计的话 没有考虑到用户体验. 我们现在要开始考虑用户体验 我们都终端用户."

Every spec from this point forward is judged through end-user lens, not engineering lens.

### UX commandments for the consumer .exe

1. **Zero questions on first run** beyond the single opt-in checkbox. No email, no PayPal, no payout method, no nothing.
2. **Every error is silent unless actionable.** Tray icon goes yellow with a one-line tooltip ("network slow, retrying"). No popups, no modals, no scary dialogs.
3. **Default settings are correct for 90% of EPal companions.** Don't expose 27 preference toggles.
4. **Visible feedback within 2 seconds** of every user action. Click install → progress bar. Click opt-in → tray icon appears. Game launches → tray glows red.
5. **No technical vocabulary in UI**. Not "tarball", not "lint", not "buyer-spec", not "PRD". Use "clip", "save", "ready", "pending".
6. **Failure is invisible to user when retryable**. Network blip → silent retry. Disk full → silent cleanup. Game version unsupported → silent pause + tray-tooltip note.
7. **One uninstall click leaves zero trace**. No leftover registry keys, no orphan files, no system-tray ghost.
8. **English-first UI but ready for zh-CN** (G252 ships post-v0.2.0; for v0.2.0 first batch is English).

### What "UX did not get considered before" means concretely

Earlier specs assumed the user would:
- Read a runbook ❌ Real users don't
- Open a terminal ❌ Real users don't
- pip install something ❌ Real users don't
- Edit `server.properties` ❌ Real users don't
- Notice a CLI exit code ❌ Real users don't
- File a GitHub issue when something breaks ❌ Real users don't

For v0.2.0, every "user has to do X" gets re-evaluated. If X is anything beyond "double-click installer + click Continue + play games", it's the WRONG design.

### UX gate for every spec going forward

Before any new spec ships, ask:
- Could a 50-year-old EPal companion who plays Minecraft for clients do this without help?
- Does this require typing anything into a terminal?
- Does failure produce a stack trace or a friendly message?
- Is there a tray-icon path to recover without restarting?

If any answer is wrong, the spec needs a UX redesign before it ships.
