# Rust One-Click Headless Decision

Task: bundle the proven `howardleegeek/gamedata-recorder` release asset
`v2.6.0/gamedata-recorder-windows-x64.zip` into the existing one-click
installer shell without touching Rust capture code.

## Decision

Do not ship `GAMEDATA_CI_MODE=1`.

`v2.6.0` logs the CI banner as:

`CI MODE ACTIVE — consent auto-granted, whitelist bypassed, this build must NOT ship to end users`

The shippable one-click path now starts the Rust recorder in normal mode and
pre-seeds `%APPDATA%\GameData Recorder\config.json` as UTF-8 without BOM:

```json
{
  "credentials": {
    "hasConsented": true,
    "consentGivenAtVersion": "2.6.0"
  },
  "preferences": {
    "autoUploadOnCompletion": false,
    "recordMicrophone": false,
    "recordingLocation": "%LOCALAPPDATA%\\GameData Recorder\\recordings",
    "games": {
      "javaw": {
        "use_window_capture": false,
        "capture_mode": "game_hook"
      },
      "minecraft": {
        "use_window_capture": false,
        "capture_mode": "game_hook"
      }
    }
  }
}
```

`preferences` is camelCase because `Preferences` uses
`#[serde(rename_all = "camelCase")]`; `GameConfig` is not camel-cased, so the
real per-game keys are `use_window_capture` and `capture_mode`.

## Evidence

Consent can be pre-granted without CI mode. `v2.6.0:src/config.rs:523-541`
defines `credentials.hasConsented` and `credentials.consentGivenAtVersion`.
`v2.6.0:src/config.rs:681-692` grants consent only when the stored semver
matches the running package version, and `v2.6.0:src/config.rs:722-728` builds
the runtime `ConsentGuard` from that config when CI mode is not active.

CI mode is not acceptable for a shipped build. `v2.6.0:src/main.rs:95-106`
emits the "must NOT ship" warning. `v2.6.0:src/config.rs:738-743` documents
that CI mode auto-grants consent, treats any foreground non-null HWND as
recordable, bypasses `GAME_WHITELIST` and game-shape checks, and only then
honors `GAMEDATA_OUTPUT_DIR`.

There is no config key that adds to the whitelist. Minecraft is already in the
compiled whitelist: `v2.6.0:crates/constants/src/lib.rs:167-170` contains
`javaw` and `minecraft`. Normal mode still checks that whitelist in
`v2.6.0:src/record/recorder.rs:490-499` and process scanning uses the same list
in `v2.6.0:src/record/recorder.rs:526-536`. The one-click config therefore
sets capture behavior for the already-whitelisted stems; it does not bypass or
extend the whitelist.

Important limitation: `v2.6.0` also cannot narrow the compiled whitelist to
Minecraft-only through config. Normal mode can still auto-record any executable
already present in `GAME_WHITELIST` if it passes the normal game-window and
game-shape checks. This is much narrower than CI mode's "any foreground HWND"
bypass, but strict "only MC and nothing else" requires a recorder source patch
that adds a configurable allowlist or a launch-scoped target process filter.

`GAMEDATA_OUTPUT_DIR` is CI-only. `v2.6.0:src/config.rs:768-787` returns the env
override only when `ci_mode()` is true, and `v2.6.0:src/main.rs:144-164` applies
that override in memory. In normal mode the default is
`%LOCALAPPDATA%\GameData Recorder\recordings` from
`v2.6.0:src/config.rs:334-339`; `v2.6.0:src/config.rs:366-421` rejects
recording locations outside LocalAppData, and `v2.6.0:src/config.rs:1094-1111`
resets invalid stored paths on load.

Finished Rust recorder sessions land under:

`%LOCALAPPDATA%\GameData Recorder\recordings\session_YYYYMMDD_HHMMSS_<8hex>\`

The recorder builds session folders from `preferences.recording_location` in
`v2.6.0:src/tokio_thread.rs:82-95`, writes `recording.mp4` and `inputs.jsonl`
in `v2.6.0:src/record/recording.rs:146-153`, and writes `metadata.json` in
`v2.6.0:src/record/local_recording.rs:621-623`.

`GAMEDATA_SKIP_API_KEY` is not CI-only, but the shipped one-click launcher does
not need it. `v2.6.0:src/tokio_thread.rs:1765-1777` uses it only to allow
auto-upload when no API key exists, and `v2.6.0:src/ui/views/mod.rs:355-379`
shows the login routing is disabled while local recording is the focus. We set
`preferences.autoUploadOnCompletion=false`, so offline recording is not blocked
by login or an API key.

## Rejected Paths

Shipping CI mode is rejected because it bypasses consent and whitelist/game
shape checks and the binary itself declares that path non-shippable.

Putting recordings under `Documents\OysterClips\sessions` through
`recordingLocation` is rejected for `v2.6.0` because the normal-mode loader
resets paths outside LocalAppData. Moving normal-mode output to Documents would
require a small source patch and rebuild of the recorder.

Using `GAMEDATA_SKIP_API_KEY` as a no-login switch is rejected for default
one-click launch because local recording already works without login, and
auto-upload is explicitly disabled.

Strict Minecraft-only auto-recording is not achievable by config alone in
`v2.6.0`. The honest alternatives are either: accept normal-mode compiled
whitelist behavior for the one-click release, or patch/rebuild the recorder with
a minimal `allowed_process_stems`/target-PID gate and keep CI mode disabled.
