# v0.28.0 First-Install Validation Playbook

> **For**: Howard, the first time he installs `OysterRecorder-Setup-v0.28.0-rc<N>.exe` on minipc and validates the consumer flow. Iron-law: every step has a green/red signal you can read directly. No guessing.

## Stage 0 — Prerequisites (one-time)
- Minipc on Tailscale (already)
- SSH from Mac works (already)
- ~600 MB free in `%LOCALAPPDATA%` (you have it)

## Stage 1 — Install (~5 min)
**One Mac command:**
```bash
RELEASE_TAG=recorder-v0.28.0-rc<N> bash bin/minipc_v028_install.sh
```

What happens:
1. Mac asks GitHub for the Release assets list
2. Minipc downloads `OysterRecorder-Setup-recorder-v0.28.0-rc<N>.exe` directly
3. Minipc verifies SHA-256 against the manifest (hard-fail if mismatch)
4. Installer wizard appears in your Session 1 — **click Next → Install → Finish**
   The Finish page launches `OysterPlay.exe` by default, which opens the
   bundled Minecraft instance directly.
5. Mac polls minipc until install completes

**Green signal**: orchestrator prints `[OK] install complete` + post-install paths exist.

## Stage 2 — Visual check on minipc (~30 sec)
On your real desktop you should see ONE new icon: **`Oyster Recording`** with the OysterRecorder icon.

`%LOCALAPPDATA%\OysterRecorder\` should contain:
```
jre\bin\javaw.exe                                    # bundled Java 21
mc-instance\versions\1.21.4\1.21.4.jar               # vanilla MC
mc-instance\versions\fabric-loader-0.16.10-1.21.4\   # Fabric profile
mc-instance\mods\oyster-recorder-mod-*-mc1.21.4.jar  # the recording mod
OysterRecorder-onedir\OysterRecorder-onedir.exe       # recorder
OysterPlay.exe                                        # NEW one-button launcher
```

**Red signal**: any path missing → tell me exactly which, I'll patch + rc<N+1>.

## Stage 3 — First Launch (~2 min)
If the Finish-page launch was not cancelled, this starts automatically.
Otherwise, double-click `Oyster Recording` on your desktop.

What happens (visible to you):
1. (~5 sec) brief "loading" — pythonw spawns OysterPlay
2. (~10 sec) OysterPlay starts the recorder and opens bundled Minecraft
3. (~30 sec) MC main menu fully renders
4. Recorder arms automatically, waits for the real game window, then iconifies

What happens (invisible — I verify via log):
- `OysterRecorder.log` writes new boot block at home dir
- `mc-instance/logs/latest.log` shows `Loading Minecraft 1.21.4 with Fabric Loader`
- `Documents/OysterClips/active_session/game_state.jsonl` starts existing (mod loaded ✅)

## Stage 4 — Record Session (~1 min)
1. **Click Singleplayer → Create New World → Default settings → Create**
2. **Play 30 sec** (walk, jump, mine a block — anything)
3. **ESC → Save and Quit to Title → Quit Game**

What recorder does (auto):
- Ignores Minecraft Launcher/pre-game windows
- Waits for a real, game-sized `Minecraft 1.21.4` window to stay stable
- Captures via window-area ffmpeg (locale-blind)
- Mod streams game-state JSON lines to `game_state.jsonl`
- On MC quit → packages session as `clip-YYYYMMDD-HHMMSS.tar.gz` in `Documents\OysterClips\`

**Green signal**: `~\OysterRecorder.log` shows:
```
mod_bridge: connected
package: real game-state JSONL found, <N> samples — overlay enabled
package: wrote session_manifest.json
upload_session: POST status=200
```

## Stage 5 — Iron-Law Verification (~30 sec, my side)
After your 30-sec session, send me a message and I'll SSH in to verify:

```
✅ tarball size 5-50 MB (sane for 30-sec capture)
✅ tarball contains video.mp4 + inputs.jsonl + game_state.jsonl + manifest.json
✅ manifest.data_authenticity == "real" (not "placeholder")
✅ supabase upload status 200
✅ session row visible in production DB
```

If all 5 green: **🥇 GOLD — first production-grade session captured + uploaded**.

## Failure Modes & Quick Fixes

| Symptom | Fix |
|---|---|
| Installer SHA mismatch | Hard-fail per iron-law. Tell me, I'll re-build CI |
| MC main menu doesn't appear within 60s | Java/Fabric crash — `~\OysterRecorder.log` will show launcher stderr — send to me |
| `mod_bridge: connected` never appears | Mod failed to load — check `mc-instance\logs\latest.log` for mod scanner errors |
| `package: hard-fail "Real game-state Fabric mod not loaded"` | Mod loaded but game_state.jsonl empty — likely you quit before any tick fired |
| `upload_session: POST failed` | Network or backend issue — separate path from today's 0x0.st bug |

— Howard Li, Oysterworld Inc, 2026-05-08
