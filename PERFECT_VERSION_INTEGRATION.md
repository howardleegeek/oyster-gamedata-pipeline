# Perfect Tester Version — Integration Manifest

*2026-05-26 PT, target = v0.11.20 bundled .exe with everything*

---

## What "perfect" means (8 criteria)

| # | Criterion | Source | Status |
|---|-----------|--------|--------|
| 1 | Small / acceptable download | R05E 738 MB bundled OR S130 55 MB Inno | 🟡 738 MB bundled chosen |
| 2 | Bundled MC client (vanilla 1.21.4) | R05E pipeline (`fetch_mc.py`) | ✅ in v0.11.19 |
| 3 | Bundled JRE 21+ | R05E pipeline (`fetch_jre.py`) | ✅ in v0.11.19 |
| 4 | VC++ Redist auto-install | **S131** (commit `bfc6364`) | 🟡 build in_progress |
| 5 | Launcher-detect gate (skip MinecraftLauncher.exe) | Commit `16f95b5` | ✅ in v0.11.19 |
| 6 | Stable state machine (no rapid-fire loop) | Commit `16f95b5` (same fix) | ✅ in v0.11.19 |
| 7 | OysterPlay auto-launch after install | Commit `5a4c3f3` | ✅ in v0.11.19 |
| 8 | 104 PRD compliance checks | PR #23 d923931 (5/18 v0.4.0+) | ✅ baked in since v0.4.0 |

---

## Integration plan (5 lines)

```
v0.11.19 (1ad56045) has criteria 2, 3, 5, 6, 7, 8
main HEAD (bfc6364) = v0.11.19 + criterion 4 (S131)
→ Next R05E build on main HEAD = ALL 8 criteria ✅
→ Auto-tag bot tags v0.11.20
→ Direct link = PERFECT version for tester
```

---

## Build status (as of dispatch)

| Pipeline | Workflow | Status | Outcome |
|----------|----------|--------|---------|
| **R05E** | Build Recorder Bundled Installer | in_progress | → v0.11.20 PERFECT 738 MB |
| **Inno** | Build Recorder (Windows) | in_progress | → backup 55 MB (no MC bundle) |

---

## Tester instruction (final, copy-paste)

```
🦪 Oyster GameData Recorder — Internal Test Build

Download: <PASTE v0.11.20 .exe URL when build completes>
Size: ~738 MB (includes Minecraft 1.21.4 + Java 21 + Recorder + VC++ Redist)
SHA256: <will be in SHA256SUMS.txt>

What it does (0 prerequisites — install + click play):
  1. Run setup.exe
  2. SmartScreen warning → "More info" → "Run anyway"
  3. Installer auto-installs VC++ Redist (one-time, silent)
  4. Installer extracts MC + JRE + Recorder to LocalAppData
  5. Click "Launch OysterPlay" on completion screen
  6. OysterPlay auto-starts bundled Minecraft 1.21.4
  7. Recorder waits for actual game window (NOT launcher) ← key fix
  8. You play → it records → .mp4 + session data saved
  9. Quit MC → recorder finalizes session
 10. Check Documents/OysterClips/ for results

Known limits:
  - SmartScreen warning (no EV signature yet, $300/yr planned)
  - 738 MB download (acceptable on broadband, painful on weak network)
  - Only Minecraft 1.21.4 supported (bundled version)
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
