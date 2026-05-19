# Mac→minipc Auto-Test for recorder-v0.27.0-rc1

> Howard 2026-05-08 — semi-automated end-to-end validation of the recorder
> on real Windows hardware. Mac orchestrates everything except the 30-sec
> gameplay; the human plays once and the rest is hands-off.

---

## What it does

`bin/minipc_v027_autotest.sh` runs 7 stages, all foreground / fail-loud:

```
Stage 1  ──  SSH + Tailscale liveness; HTTP 200 on all release URLs
Stage 2  ──  minipc downloads .exe + matching mod jar from GitHub Releases
             directly (no Mac round-trip), reports SHA-256 back
Stage 3  ──  Verify SHA against the manifest published with the release
             (mismatch = hard-fail, never proceed)
Stage 4  ──  Detect Minecraft + Fabric loader installation; instruct user
             if MC missing or Fabric not installed for the target version
Stage 5  ──  Drop the mod jar into %APPDATA%\.minecraft\mods\
Stage 6  ──  Print READY banner with 7 manual steps; wait for user keypress
             (this is the one un-automatable part — a human must play 30 s)
Stage 7  ──  Pull OysterRecorder_diagnostic.zip back to Mac via PowerShell
             base64 stream; hand off to recorder_autoloop.sh for analysis
```

---

## Why semi-auto and not fully auto

Three architectures considered:

| Option | Auto level | Practical? |
|---|---|---|
| Full auto: mineflayer bot + headless MC client | 100 % | ❌ MC Java client needs a Windows desktop session, hard to drive from SSH |
| **Semi-auto: Mac orchestrates everything except 30-sec gameplay** | 95 % | ✅ Picked |
| Full auto: PowerShell UIAutomation key-sends | 100 % | ⚠️ Fragile; breaks every MC version update |

Semi-auto is **iron-law-honest**: the human visually confirms what's being recorded is real Minecraft (not a synthetic / fake capture). Auto-orchestration covers the boring layers (transfer, SHA verify, MC version check, diag-zip retrieval, autoloop hand-off).

---

## Iron-law guarantees

1. **Every SHA-256 is verified against the release manifest.** Mismatch = hard-fail with the expected vs got hashes. No silent continue.
2. **MC version detected via filesystem inspection** (`%APPDATA%\.minecraft\versions\fabric-loader*<MC>\`), not user prompt. Cannot be lied to.
3. **Diag zip retrieval verifies mtime is post-script-start.** Defensive against stale zips from a previous (failed) run polluting the analysis.
4. **All commands run in foreground.** SSH disconnect mid-run = visible failure, not silent partial state.
5. **No fabricated "test passed".** The only success signals are: real HTTP 200, real SHA match, real diag zip mtime, real autoloop classification report.

---

## Pre-flight (one-time on minipc)

```
Tailscale connected (tailscale status should show this Mac)
SSH key from Mac authorized for `howard.linra@minipc-bwdxs`
Minecraft Java 1.21.4 stable installed (NOT a snapshot — mod won't load)
Fabric loader 0.16.0+ installed for that version
```

If any are missing, the script tells you which.

---

## Usage

```bash
cd ~/Downloads/oyster-agent-runner

# Connectivity check only (no push, no install):
DRY_RUN=1 ./bin/minipc_v027_autotest.sh

# Full flow (will pause at Stage 5 for you to play):
./bin/minipc_v027_autotest.sh

# Override target MC version (default 1.21.4):
MC_VERSION=1.20.4 ./bin/minipc_v027_autotest.sh

# Override release tag (default recorder-v0.27.0-rc1):
RELEASE_TAG=recorder-v0.27.0 ./bin/minipc_v027_autotest.sh
```

---

## What success looks like

```
Stage 1 ─ SSH/release URLs all green
Stage 2 ─ FETCHED:OysterRecorder.exe:597ff3b22a83f4...
          FETCHED:oyster-recorder-mod-...mc1.21.4.jar:6d3638372964...
Stage 3 ─ [OK] SHA-256 verified: OysterRecorder.exe
          [OK] SHA-256 verified: oyster-recorder-mod-...mc1.21.4.jar
Stage 4 ─ [OK] Fabric loader found: fabric-loader-0.16.10-1.21.4
Stage 5 ─ [OK] mod jar installed
Stage 6 ─ READY TO RECORD banner — human plays 30-60 s, presses Enter
Stage 7 ─ diag zip retrieved: ~/Downloads/OysterRecorder_diagnostic_<TS>.zip
          [OK] autoloop running (PID …)
          [OK] watch progress: tail -f /tmp/recorder-autoloop.log
          [OK] minipc auto-test complete
```

Within ~60 s of the autoloop seeing the new diag zip, you'll see an
auto-spec at `specs/auto/R-AUTO-<timestamp>.md` with classified issues.

**If the test passed iron-law:** the spec should report 0 critical issues
(no FULL_DESKTOP_CAPTURE, no PLACEHOLDER_GAMESTATE). Lower-severity
findings (audio device missing etc) are expected and benign.

---

## What success does NOT mean

- This validates the recorder + mod path on real Windows.
- It does NOT validate the upload flow (gap #6 HMAC + gap #8 direct-to-Supabase still pending in PRODUCTION_GAPS.md).
- It does NOT validate Stripe payouts (gap #4).
- It does NOT validate buyer purchase (gap #4).

For those, see `PRODUCTION_LAUNCH_SOP.md` Stage 4 once Howard's hands-required items land.

---

## Pairs with

- [`bin/recorder_autoloop.sh`](../bin/recorder_autoloop.sh) — auto-classifies the diag zip retrieved by Stage 7
- [`bin/recorder_log_analyzer.py`](../bin/recorder_log_analyzer.py) — pattern catalogue for issue classification
- [`docs/AUTO_HEAL_LOOP.md`](AUTO_HEAL_LOOP.md) — closed-loop architecture
- [`docs/AUTO_DETECTION.md`](AUTO_DETECTION.md) — unified 6-layer detection
- [`PRODUCTION_LAUNCH_SOP.md`](../PRODUCTION_LAUNCH_SOP.md) — full launch playbook

— Howard Li, Oysterworld Inc, 2026-05-08
