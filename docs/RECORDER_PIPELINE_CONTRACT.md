# Recorder Pipeline Contract

This repo is the integration hub between two real systems:

1. **OysterRecorder** (`vendor/recorder/` and the Windows installer release path) captures screen, input, logs, and raw session folders.
2. **oyster-gamedata-pipeline** normalizes those sessions into buyer-spec bundles, runs gates, signs provenance, and uploads/verifies distribution.

The product works only if both sides agree on the session bundle surface. The authoritative code contract is `src/oyster_agent_runner/session_contract.py`.

## Version Alignment

There are two version tracks and they must not be confused:

| Component | Current release track | Current source track |
|---|---|---|
| `oyster-gamedata-pipeline` | Latest GitHub release remains `v0.11.20`; it carries the public Windows installer asset and checksum. | `main` continues from `v0.11.20` with architecture/CI contract fixes. Use `main` for development. |
| `gamedata-recorder` | Latest recorder release remains `v2.6.0`; this is why the public installer asset is named `OysterRecorder-setup-v2.6.0.exe`. | `vendor/recorder` is pinned to release-buildable commit `e171f20` (`release/v2.6.0-buildable`), based on the last successful Windows-build source plus no-popup runtime guards and detected-game HWND stabilization. Arbitrary recorder `main` changes are not consumer-release source until tray/auth/updater/notify/libobs changes compile and pass Windows installer smoke. |

Operationally: distribute from the latest verified release, rebuild from the
release-buildable source pin, and keep newer recorder `main` work behind a
promotion gate. A new consumer release should only be cut after the recorder
source pin has passed Windows build/installer smoke.

Release assets have their own contract in
[`docs/RELEASE_CHANNELS.md`](RELEASE_CHANNELS.md). The short rule is: `v0.x`
consumer releases feed the public appcast, while the rc19 bundled recorder line
is a QA/reference fallback until it is rebuilt and promoted through the normal
consumer installer gates.

## Layouts

| Layout | Owner | Required paths | Consumer |
|---|---|---|---|
| `lem` | Windows recorder | `recordings/main_record.mp4`, `streams/states.jsonl` | `bin/real_session_validator.py` maps it into the legacy validation view. |
| `legacy_pipeline` | Python pipeline / game adapters | `recording.mp4`, `game_state.jsonl` | `bin/canonical_pipeline.py`, sync/input/quality gates, provenance scripts. |
| `phase1_agent` | Mineflayer/agent capture path | `manifest.json`, `cot.jsonl`, `metadata.jsonl`, `inputs.jsonl` | `src/oyster_agent_runner/buyer_spec_adapter.py`. |
| `buyer_prd` | Final buyer package | `video.mp4`, `systeminfo.json`, `action_camera.json`, `gameinfo.xlsx`, `depth/` | Vendors, buyers, final audits, manifests. |

## Flow

```text
Windows recorder raw session
  lem/
    recordings/main_record.mp4
    streams/states.jsonl
    streams/actions.jsonl
        |
        v
temporary validation view
  legacy_pipeline/
    recording.mp4
    game_state.jsonl
    inputs.jsonl
        |
        v
canonical pipeline + G-gates
        |
        v
buyer_prd/
  video.mp4
  systeminfo.json
  action_camera.json
  gameinfo.xlsx
  depth/*.exr
```

## Promotion Rule For New Games

A game profile can move to `smoke_ready` only after it emits either:

- a complete `legacy_pipeline` session, or
- a complete `lem` session that maps cleanly into `legacy_pipeline`.

It can move to `production` only after a clean Windows run proves:

1. latest GitHub release has the installer and `SHA256SUMS.txt`;
2. backend health and `/api/v1/updates/appcast.xml` are green;
3. real install, tray launch, gameplay capture, upload, validation, and uninstall pass on a clean Windows machine;
4. final output satisfies the `buyer_prd` contract.

## Current Architecture Read

The release/distribution chain is real: latest verified public release is
`v0.11.20`, the installer asset and SHA file exist, backend health/appcast are
live, and smoke workflows are green. The Windows installer is built from the
release-buildable source pin plus the verified x64 recorder runtime, with PE
architecture guards to prevent ARM64 DLLs from entering the setup payload. The
remaining production risk is
release/source drift plus data-contract drift while expanding from Minecraft
into BeamNG, Factorio, Stardew Valley, and other single-player games.

Keep new game work thin:

1. add or update `src/oyster_agent_runner/game_plugins.py`;
2. implement the adapter under `src/oyster_agent_runner/environments/`;
3. prove it satisfies `session_contract.py`;
4. run targeted adapter tests plus real-session validator smoke.
