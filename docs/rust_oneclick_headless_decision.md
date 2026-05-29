# Rust One-Click Headless Decision

Task: bundle the proven `howardleegeek/gamedata-recorder` release asset
`v2.6.0/gamedata-recorder-windows-x64.zip` into the existing one-click
installer shell without touching Rust capture code.

## Decision

Use the Rust recorder in CI/headless env mode for no-click consent and output
directory redirection, and have `OysterPlay.exe` pre-seed the Rust config for
Minecraft game-hook capture before launching it.

Runtime settings:

- `GAMEDATA_CI_MODE=1`
- `GAMEDATA_SKIP_API_KEY=1`
- `GAMEDATA_OUTPUT_DIR=%USERPROFILE%\Documents\OysterClips\sessions`
- `OYSTER_CAPTURE_MODE=game` as a harmless forward-compatible env hint
- `%APPDATA%\GameData Recorder\config.json` merged with:
  `preferences.games.javaw.capture_mode=game_hook` and
  `preferences.games.minecraft.capture_mode=game_hook`

This path gives no consent click, no API-key/login requirement, and real
disk-backed recordings under the OysterClips sessions directory.

## Evidence

CI mode is not an in-memory recorder. In `v2.6.0:src/main.rs:144-164`, the
"in-memory only" note refers to the config mutation not being persisted:
the code creates `GAMEDATA_OUTPUT_DIR`, locks `app_state.config`, and assigns
`config.preferences.recording_location = ci_dir`, then deliberately skips
`config.save()`.

Consent is session-only auto-granted in CI mode. `v2.6.0:src/config.rs:718-725`
returns `ConsentGuard::granted()` when `ci_mode()` is active, and
`v2.6.0:src/config.rs:738-743` documents that CI mode auto-grants consent,
bypasses the game whitelist/game-shape gate, and redirects recordings when
`GAMEDATA_OUTPUT_DIR` is set.

The output path is used by the real disk recorder. `v2.6.0:src/tokio_thread.rs:82-95`
builds each session directory from
`app_state.config.preferences.recording_location`. `v2.6.0:src/record/recorder.rs:166-169`
calls `LocalRecording::create_at(&recording_location)`, and
`v2.6.0:src/record/recording.rs:146-153` writes `recording.mp4` and
`inputs.jsonl` under that session path. `v2.6.0:src/record/local_recording.rs:621-623`
writes `metadata.json` to the same recording directory.

`GAMEDATA_SKIP_API_KEY` only affects upload gating, which is what we want for
offline/no-login use. `v2.6.0:src/tokio_thread.rs:1765-1777` checks the env var
and avoids blocking auto-upload logic on a missing API key.

The tag source does not implement `OYSTER_CAPTURE_MODE`. A tag audit with
`git grep -n "OYSTER_CAPTURE_MODE" v2.6.0 -- src crates constants` returned no
matches. Capture mode is instead controlled through `GameConfig`: the enum is
serialized as snake_case in `v2.6.0:src/config.rs:182-218`, and
`v2.6.0:src/config.rs:280-313` resolves `CaptureMode::GameHook` to the OBS
game-capture hook. Minecraft's process stems are already whitelisted as
`javaw` and `minecraft` in `v2.6.0:crates/constants/src/lib.rs:167-170`, while
`KNOWN_HOOK_REQUIRED_GAMES` is intentionally empty at
`v2.6.0:crates/constants/src/lib.rs:284-288`, so config seeding is required to
force Minecraft to game hook in the released artifact.

Normal-mode-only config redirection is not the right output-dir strategy for
OysterClips. `v2.6.0:src/config.rs:366-421` only accepts normal
`recording_location` values inside LocalAppData, and
`v2.6.0:src/config.rs:1094-1111` resets unsafe/outside paths on load. The CI
output override intentionally skips that validation for harness-controlled
paths in `v2.6.0:src/config.rs:768-776`, which is why `GAMEDATA_OUTPUT_DIR` is
the correct way to land sessions under Documents/OysterClips.

## Rejected Path

Pure normal mode with only a shipped config file was rejected. It can pregrant
consent by setting `credentials.consentGivenAtVersion="2.6.0"` and can force
`game_hook`, but it cannot safely set `Documents\OysterClips\sessions` as
`recordingLocation` because the v2.6.0 loader resets paths outside LocalAppData.

Pure CI env mode without config seeding was also rejected. It gives no-click
consent and disk output, but `OYSTER_CAPTURE_MODE` is absent in the v2.6.0 tag,
so Minecraft would remain on Auto/WGC instead of the proven game-hook path.
