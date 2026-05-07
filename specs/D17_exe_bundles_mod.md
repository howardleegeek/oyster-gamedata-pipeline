---
task_id: D17
project: oyster-gamedata-pipeline
priority: 1
estimated_minutes: 90
depends_on: [D15 client mod, recorder-v0.25.0-real-depth]
modifies:
  - bin/recorder_consumer_lite.py  (mod auto-install on first run)
  - bin/install_fabric_loader.py  (new helper)
  - .github/workflows/build-recorder-exe.yml  (add mc-mod jar to PyInstaller bundle)
must_not_touch:
  - mc-mod/  (build it, don't change it)
  - .exe distribution UX (still double-click-and-play)
executor: glm
iron_law: REAL ONLY — testers must get REAL game-state without manual mod install
iron_law_waived: spec body describes the placeholder gap being closed (legitimate iron-law discussion)
---

# D17: .exe auto-installs Fabric loader + Oyster mod

## 目标

When tester downloads `OysterRecorder.exe` and double-clicks, current
behaviour:
1. Recorder starts in tray
2. Tester opens MC manually → plays → recorder packs tarball

Problem: tester must MANUALLY install Fabric loader + drop the mod jar
into `%APPDATA%\.minecraft\mods\` before they get real game-state. 90% of
testers won't do this. Result: action_camera fields stay placeholder,
which violates the iron law.

This spec makes the .exe auto-install everything on first launch:
1. Detect `%APPDATA%\.minecraft\` exists (= MC Java edition installed)
2. Detect Fabric loader present in `versions/` — if not, run
   `fabric-installer-X.Y.Z.jar` headlessly
3. Drop bundled `oyster-recorder-mod-X.Y.Z.jar` into `mods/`
4. If MC was open → prompt user "restart MC for real-data recording"
5. Future launches: just verify the jar is current; replace if newer

## Architecture

```
OysterRecorder.exe (PyInstaller bundle includes:)
  ├── recorder_consumer_lite.py (existing)
  ├── install_fabric_loader.py (new — run on first launch)
  ├── bundle/oyster-recorder-mod-X.Y.Z.jar (from mc-mod GHA)
  └── bundle/fabric-installer-1.0.1.jar (downloaded at build time)

First-run flow (in recorder_consumer_lite.py main()):
  1. Locate .minecraft path:
       Windows: %APPDATA%\.minecraft
       macOS:   ~/Library/Application Support/minecraft
       Linux:   ~/.minecraft
  2. If not found: warn-once tray notification, continue without mod
  3. Run install_fabric_loader.py (idempotent)
  4. Copy bundle/*.jar into mods/, replacing older versions
  5. Set state file ~/Documents/OysterClips/.fabric_install_done
```

## 验收标准 (REAL ONLY)

- [ ] `bin/install_fabric_loader.py` is a standalone Python script (no
      external deps beyond stdlib + `urllib`) that:
      - Detects MC install path cross-platform
      - Verifies + installs Fabric loader 0.16.x for MC 1.21.4 (skip if
        present)
      - Drops mod jar into `mods/` (clobber older Oyster jars only)
      - Returns dict with `{installed, mc_version, fabric_version, mod_path}`
- [ ] `recorder_consumer_lite.py` calls it once per session, fails-soft
- [ ] `.github/workflows/build-recorder-exe.yml`:
      - Downloads latest `oyster-recorder-mod-*.jar` artifact from D15 GHA
      - Bundles into PyInstaller `--add-data` paths
      - Bundles `fabric-installer-1.0.1.jar` (download from official
        releases at build time)
- [ ] On a fresh Windows test box: launching the .exe once → opening MC
      → game_state.jsonl gets written (manual smoke test, doc'd in spec)
- [ ] PyInstaller .exe size < 600 MB (currently 406 MB; +mod ~200 KB +
      fabric installer ~5 MB)
- [ ] D5 on a tester-uploaded tarball reports `[REAL] action_camera —
      real_game_state=true` after this lands

## REAL artifact criterion

- Mod must actually run inside MC. NO synthetic JSONL fakery.
- Fabric installer call must succeed against MC 1.21.4 official manifest;
  no hardcoded jar contents.
- If MC install absent: clear notification, never silent placeholder.

## 不要做

- 不要修改用户的 vanilla MC profile config — only create a new
  `oyster-fabric-X.Y.Z` profile alongside
- 不要 download Fabric installer at runtime — bundle at build time
  (offline-friendly, more deterministic)
- 不要 install mod for MC server installs (only client `.minecraft/`)
