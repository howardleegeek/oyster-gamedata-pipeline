# Perfect Tester Version — Integration Manifest

*2026-05-26 PT — **v0.11.20 SHIPPED**, all 8 criteria verified in artifact*

---

## Perfect URL (verified)

```
https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/v0.11.20/OysterRecorder-Setup-recorder-v0.28.0-rc19.0.6.exe
```

- Size: **738 MB** (774,306,215 bytes)
- SHA256: `57c1f7efa38a76fdaa2fe44bdcb8d9746488932bd8728bc3d7238df1c701a397`
- Built from commit `bfc6364` (S131 VC++ Redist included); functional content identical to v0.11.20 tag commit `9dbed18` (only docs + version-string commits between them)

---

## What "perfect" means (8 criteria) — all ✅

| # | Criterion | Evidence at v0.11.20 commit `9dbed18` |
|---|-----------|----------------------------------------|
| 1 | Bundled MC client (vanilla 1.21.4) | `bin/build_bundled_installer/fetch_minecraft.py` + `manifest.json:mc_pin` |
| 2 | Bundled JRE 21+ | `bin/build_bundled_installer/fetch_jre.py` + `manifest.json` root pin |
| 3 | VC++ Redist auto-install (silent) | S131 commit `bfc6364` — `installer/oyster-recorder.iss` + `bin/build_bundled_installer/installer.iss` `[Files]` + `[Run]` entries |
| 4 | Launcher-detect exclusion | `bin/recorder_consumer_lite.py:517` — `MC_PROCESS_NAMES = {"javaw.exe", "java.exe", "Minecraft.exe"}` with explicit `MC_LAUNCHER_PROCESS_NAMES = {"minecraftlauncher.exe"}` denylist |
| 5 | Bilingual launcher title filter | `bin/recorder_consumer_lite.py` — `MC_WINDOW_TITLE_EXCLUDE_MARKERS = ("launcher", "启动器")` (handles Chinese MC launcher) |
| 6 | Window-size + stability gate | `vendor/recorder/src/tokio_thread.rs:984` `StabilityTracker` — ≥1280×720 stable 10s + process alive ≥20s before auto-record |
| 7 | State-machine sanity (no rapid-fire loop) | Same as #4+#6: process-name denylist + Rust stability tracker; tests at `tests/bin/test_one_click_consumer_flow.py:66-71` verify launcher rejection |
| 8 | 104 PRD compliance checks | Baked into pipeline since v0.4.0 (PR #23 d923931, 2026-05-18) |

---

## Build provenance

| Run | Workflow | HEAD | Conclusion | Time |
|-----|----------|------|------------|------|
| `26479741368` | Build Recorder Bundled Installer (R05E) | `bfc6364` | ✅ success | 22:52:24Z → ~22:55 |
| `26479828071` | Auto Release Tagger | `417eb05` | ✅ success | 22:54:41Z → 22:56:06Z |
| `v0.11.20` tag | git ref | `9dbed18` | created | 22:55:22Z |
| `.exe` asset | attached to v0.11.20 | (from `bfc6364` build) | uploaded | 22:55:24Z |

Belt-and-suspenders: R05E run `26481791836` triggered against `main` HEAD `2090aff` (with manifest cleanup `fix(bundled-manifest): remove stale Mojang asset 01bbb775`). If it succeeds, Auto Release Tagger will likely produce v0.11.21 with a fresher manifest — functionally identical recorder behavior, just a cleaner Mojang post-fetch checklist.

---

## Tester instruction (copy-paste ready)

```
🦪 Oyster GameData Recorder — Internal Test Build (v0.11.20)

Download (738 MB, single file, no other downloads needed):
https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/v0.11.20/OysterRecorder-Setup-recorder-v0.28.0-rc19.0.6.exe

Verify SHA256 (optional but recommended):
57c1f7efa38a76fdaa2fe44bdcb8d9746488932bd8728bc3d7238df1c701a397

Install + play (zero prerequisites — no Java, no MC, nothing to install first):
  1. Double-click the downloaded .exe
  2. Windows SmartScreen: click "More info" → "Run anyway" (no EV cert yet)
  3. Installer auto-installs VC++ Redist 2015-2022 x64 (silent, one-time)
  4. Installer extracts Recorder + bundled Minecraft 1.21.4 + Java 21 to %LOCALAPPDATA%
  5. Click "Launch OysterPlay" on the completion screen
  6. OysterPlay starts the bundled Minecraft (NOT the official launcher)
  7. Recorder waits for the real game window (will NOT fire on the launcher) ← key fix
  8. Play — recorder captures video (.mp4) + inputs (.jsonl) + game-state ticks (.bin)
  9. Quit MC → recorder finalizes the session
 10. Find session output under Documents\OysterClips\

Known limits:
  • SmartScreen warning on first run (we have no EV cert yet — $300/yr planned)
  • 738 MB download size (acceptable on broadband; everything is bundled so this is one-time)
  • Only Minecraft 1.21.4 supported in this build (bundled version is fixed)
  • If you previously had the MS Launcher running, close it before installing (avoid window confusion)

Bug report channel: <Howard fills in>
```

---

## What's NOT in this perfect version (deferred)

- ❌ EV-signed installer (no SmartScreen warning) — needs $300/yr DigiCert/Sectigo
- ❌ Multi-MC-version support (only 1.21.4) — would multiply download size
- ❌ Telemetry / crash report uploads — privacy + infra work
- ❌ Auto-update (WinSparkle) — needs update server
- ❌ Real backend (Fly.io deploy) — needs FLY_API_TOKEN

These are v1.0 production concerns, not internal-test blockers.

---

🦪 Joint integration (Claude scheduler + Codex recorder + cluster pipelines)
2026-05-26 PT
